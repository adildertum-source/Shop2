import copy, html, os, json, hmac, hashlib, time, asyncio, uuid
from urllib.parse import parse_qsl
from aiohttp import web, ClientSession

TOKEN = os.getenv("BOT_TOKEN", "")
ADMINS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}
URL = os.getenv("WEBAPP_URL", "")
DEV = bool(os.getenv("DEV"))
if DEV:
    ADMINS.add(1)
PORT = int(os.getenv("PORT", 8080))
DBF = "db.json"
os.makedirs("up", exist_ok=True)

DEF = {
    "shop": {"name": "Мой Магазин", "banner": "", "info1": "Заказы с 10:00 до 2:00. Отправка за 15 минут.",
             "info2": "Выберите раздел, добавьте в корзину и оформите заказ.", "support": "username"},
    "pay": {"rates": {"uah": "1", "stars": "2", "rub": "2.2"},
            "req": {"uah": "Карта: 0000 0000 0000 0000", "stars": "@username", "rub": "Карта: 0000 0000 0000 0000"}},
    "theme": {"bg": "", "bgcol": "#0b0607", "card": "#1b1214", "op": "0.9", "line": "#34e07a", "acc": "#f0283c"},
    "wheel": {"cd": "24", "segs": [{"name": "Приз 1", "weight": "10", "photo": "", "color": "#e11d2e"}, {"name": "Ничего", "weight": "30", "photo": "", "color": "#1f7a3d"}]},
    "guide": "1. Выберите раздел и товар.\n2. Добавьте в корзину.\n3. Выберите валюту, оплатите по реквизитам и нажмите «Я оплатил».",
    "spins": {},
    "sections": [{"id": "s1", "title": "МетроШоп", "banner": "", "items": []},
                 {"id": "s2", "title": "Софты", "banner": "", "items": []}],
    "auctions": [],
    "orders": [],
}


def load():
    try:
        return json.load(open(DBF, encoding="utf-8"))
    except Exception:
        return json.loads(json.dumps(DEF))


db = load()
for _k, _v in DEF.items():
    db.setdefault(_k, json.loads(json.dumps(_v)))
if db["theme"].get("card") in ("#3a161c", "#0f1a2b"):
    db["theme"] = json.loads(json.dumps(DEF["theme"]))


def save():
    json.dump(db, open(DBF, "w", encoding="utf-8"), ensure_ascii=False)


def auth(req):
    """Проверка Telegram initData -> dict пользователя или None"""
    if DEV:
        return {"id": 1, "username": "dev"}
    raw = req.headers.get("X-Init", "")
    d = dict(parse_qsl(raw))
    h = d.pop("hash", "")
    chk = "\n".join(f"{k}={v}" for k, v in sorted(d.items()))
    key = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    if h and hmac.compare_digest(hmac.new(key, chk.encode(), hashlib.sha256).hexdigest(), h):
        return json.loads(d.get("user", "{}"))
    return None


async def tg(method, **p):
    async with ClientSession() as s:
        async with s.post(f"https://api.telegram.org/bot{TOKEN}/{method}", json=p) as r:
            return await r.json()


def lines():
    for sec in db["sections"]:
        for it in sec["items"]:
            if it.get("opts"):
                for o in it["opts"]:
                    yield it["id"] + ":" + o["id"], it["name"] + " · " + o["name"], float(o["price"] or 0), o
            else:
                yield it["id"], it["name"], float(it["price"] or 0), None


def keys_of(o):
    return [k.strip() for k in o.get("keys", "").split("\n") if k.strip()]


async def notify(text, markup=None):
    for a in ADMINS:
        try:
            await tg("sendMessage", chat_id=a, text=text, **({"reply_markup": markup} if markup else {}))
        except Exception:
            pass


def need(req):
    u = auth(req)
    if not u:
        raise web.HTTPUnauthorized()
    return u


async def get_data(req):
    u = need(req)
    out = {k: v for k, v in db.items() if k not in ("orders", "spins")}
    out["wait"] = max(0, int(db["spins"].get(str(u["id"]), 0) + float(db["wheel"]["cd"] or 0) * 3600 - time.time()))
    adm = u["id"] in ADMINS
    out["sections"] = copy.deepcopy(db["sections"])
    for sec in out["sections"]:
        for it in sec["items"]:
            for o in it.get("opts", []):
                o["stock"] = len(keys_of(o))
                o.pop("keys", None)
    dn = [x for x in db["orders"] if x.get("status") == "done"]
    out["done"] = len(dn)
    out["recent"] = [{"name": x["items"][0].rsplit(" x", 1)[0].split(" · ")[0], "t": x["t"]} for x in dn[-5:]][::-1]
    out["is_admin"] = adm
    out["my_orders"] = [o for o in db["orders"] if o["uid"] == u["id"]][-20:]
    if adm:
        out["all_orders"] = db["orders"][-50:]
    return web.json_response(out)


async def order(req):
    u = need(req)
    b = await req.json()
    items, total, ls = [], 0, []
    for lid, name, price, o in lines():
        q = int(b["cart"].get(lid, 0))
        if q > 0:
            if o is not None and q > len(keys_of(o)):
                return web.json_response({"ok": False, "err": "stock"})
            items.append(f"{name} x{q}")
            ls.append([lid, q])
            total += price * q
    if not items:
        return web.json_response({"ok": False})
    cur = b.get("cur", "uah")
    o = {"id": uuid.uuid4().hex[:6], "uid": u["id"], "user": u.get("username") or u.get("first_name", ""),
         "items": items, "lines": ls, "status": "new", "total": total, "cur": cur, "t": int(time.time())}
    db["orders"].append(o)
    save()
    r = float(db["pay"]["rates"].get(cur, 1))
    await notify(f'🛒 Заказ #{o["id"]} от @{o["user"]} ({u["id"]})\n' + "\n".join(items) +
                 f'\nИтого: {total:g} грн = {total * r:g} {cur.upper()}',
                 {"inline_keyboard": [[{"text": "✅ Оплачено — выдать", "callback_data": "ok:" + o["id"]}]]})
    return web.json_response({"ok": True, "id": o["id"]})


async def bid(req):
    u = need(req)
    b = await req.json()
    for a in db["auctions"]:
        if a["id"] == b["id"]:
            mn = float(a["bid"]) + float(a.get("step") or 1) if a.get("bid") else float(a["start"])
            amt = float(b["amount"])
            if time.time() > float(a["end"]) or amt < mn:
                return web.json_response({"ok": False})
            a["bid"], a["leader"] = amt, u.get("username") or u.get("first_name", "")
            save()
            await notify(f'🔨 Ставка {amt:g} {a.get("cur", "uah").upper()} на «{a["title"]}» от @{a["leader"]}')
            return web.json_response({"ok": True})
    return web.json_response({"ok": False})


async def spin(req):
    u = need(req)
    w = db["wheel"]
    uid = str(u["id"])
    if time.time() < db["spins"].get(uid, 0) + float(w["cd"] or 0) * 3600 or not w["segs"]:
        return web.json_response({"ok": False})
    import random
    i = random.choices(range(len(w["segs"])), weights=[max(0.0, float(x["weight"] or 0)) + 1e-9 for x in w["segs"]])[0]
    db["spins"][uid] = time.time()
    save()
    await notify(f'🎡 @{u.get("username") or u.get("first_name", "")} ({u["id"]}) выиграл: {w["segs"][i]["name"]}')
    return web.json_response({"ok": True, "i": i})


async def deliver(oid):
    o = next((x for x in db["orders"] if x["id"] == oid), None)
    if not o or o.get("status") == "done":
        return False, "Заказ не найден или уже выдан"
    lm = {l[0]: (l[1], l[3]) for l in lines()}
    for lid, q in o["lines"]:
        if lid in lm and lm[lid][1] is not None and len(keys_of(lm[lid][1])) < q:
            return False, "Не хватает ключей: " + lm[lid][0]
    parts = []
    for lid, q in o["lines"]:
        name, opt = lm.get(lid, (None, None))
        if opt is not None:
            ks = keys_of(opt)
            opt["keys"] = "\n".join(ks[q:])
            parts.append(html.escape(name) + ":\n" + "\n".join(f"<code>{html.escape(k)}</code>" for k in ks[:q]))
    o["status"] = "done"
    save()
    txt = f'✅ Заказ #{oid} оплачен, спасибо!' + ("\n\n" + "\n\n".join(parts) if parts else "\nМы свяжемся с вами.")
    await tg("sendMessage", chat_id=o["uid"], text=txt, parse_mode="HTML")
    return True, "Выдано"


async def confirm(req):
    u = need(req)
    if u["id"] not in ADMINS:
        raise web.HTTPForbidden()
    ok, msg = await deliver((await req.json())["id"])
    return web.json_response({"ok": ok, "msg": msg})


async def admin_save(req):
    u = need(req)
    if u["id"] not in ADMINS:
        raise web.HTTPForbidden()
    b = await req.json()
    old = {(it["id"], o["id"]): o.get("keys", "") for sec in db["sections"] for it in sec["items"] for o in it.get("opts", [])}
    for k in ("shop", "pay", "sections", "auctions", "theme", "wheel", "guide"):
        db[k] = b[k]
    for sec in db["sections"]:
        for it in sec["items"]:
            for o in it.get("opts", []):
                nk = [x.strip() for x in (o.pop("newkeys", "") or "").split("\n") if x.strip()]
                o["keys"] = "\n".join([x for x in old.get((it["id"], o["id"]), "").split("\n") if x.strip()] + nk)
                o.pop("stock", None)
    save()
    return web.json_response({"ok": True})


async def upload(req):
    u = need(req)
    if u["id"] not in ADMINS:
        raise web.HTTPForbidden()
    data = await req.read()
    ext = {"image/png": "png", "image/webp": "webp"}.get(req.content_type, "jpg")
    name = f"{uuid.uuid4().hex}.{ext}"
    open(f"up/{name}", "wb").write(data)
    return web.json_response({"url": f"/up/{name}"})


async def poll():
    off = 0
    while True:
        try:
            r = await tg("getUpdates", offset=off, timeout=25)
            for up in r.get("result", []):
                off = up["update_id"] + 1
                cb = up.get("callback_query")
                if cb and cb["from"]["id"] in ADMINS and cb.get("data", "").startswith("ok:"):
                    ok, msg = await deliver(cb["data"][3:])
                    await tg("answerCallbackQuery", callback_query_id=cb["id"], text=msg)
                    if ok:
                        m = cb["message"]
                        await tg("editMessageReplyMarkup", chat_id=m["chat"]["id"], message_id=m["message_id"],
                                 reply_markup={"inline_keyboard": [[{"text": "✅ Выдано", "callback_data": "x"}]]})
                m = up.get("message")
                if m and m.get("text", "").startswith("/start"):
                    await tg("sendMessage", chat_id=m["chat"]["id"], text=f'Добро пожаловать в {db["shop"]["name"]}!',
                             reply_markup={"inline_keyboard": [[{"text": "🛍 Открыть магазин", "web_app": {"url": URL}}]]})
        except Exception:
            await asyncio.sleep(3)


async def index(_):
    return web.FileResponse("index.html", headers={"Cache-Control": "no-store"})


async def start(app):
    if TOKEN and not DEV:
        asyncio.create_task(poll())


app = web.Application(client_max_size=15 * 1024 * 1024)
app.add_routes([web.get("/", index), web.get("/api/data", get_data), web.post("/api/order", order),
                web.post("/api/bid", bid), web.post("/api/confirm", confirm), web.post("/api/spin", spin), web.post("/api/admin", admin_save), web.post("/api/upload", upload),
                web.static("/up", "up")])
app.on_startup.append(start)
print("Магазин запущен, версия: красный + простая админка")
web.run_app(app, port=PORT)
