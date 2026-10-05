import os

import requests
import streamlit as st

API = os.getenv("API_URL", "http://127.0.0.1:5000")
STATUS_ICON = {"pending": "🕒", "confirmed": "✅", "preparing": "👨‍🍳", "picked_up": "🛍️",
               "out_for_delivery": "🚴", "delivered": "🎉", "cancelled": "❌"}

st.set_page_config(page_title="Foodeliver", page_icon="🍔", layout="wide")


# ------------------------------------------------------------ helpers
def call(method, path, json=None, params=None):
    h = {"Authorization": f"Bearer {st.session_state.token}"} if st.session_state.get("token") else {}
    try:
        r = requests.request(method, API + path, json=json, params=params, headers=h, timeout=10)
    except requests.RequestException as e:
        return 0, {"error": f"API not reachable: {e}"}
    try:
        data = r.json() if r.content else {}
    except ValueError:
        data = {}
    return r.status_code, data


def ok(res, msg=None):
    code, data = res
    if code and code < 400:
        if msg:
            st.toast(msg)
        return True
    st.error(data.get("error") or data.get("msg") or f"Error {code}")
    return False


def money(x):
    return f"Rs {x:,.0f}"


# ------------------------------------------------------------ auth
def auth_screen():
    st.title("🍔 Foodeliver")
    t1, t2, t3 = st.tabs(["Login", "Sign up", "Forgot password"])
    with t1:
        with st.form("login"):
            email, pw = st.text_input("Email"), st.text_input("Password", type="password")
            if st.form_submit_button("Login"):
                code, d = call("POST", "/api/auth/login", {"email": email, "password": pw})
                if code == 200:
                    st.session_state.token, st.session_state.user = d["token"], d["user"]
                    st.rerun()
                st.error(d.get("error", "Login failed"))
    with t2:
        with st.form("signup"):
            name, email = st.text_input("Name"), st.text_input("Email ", key="se")
            phone, pw = st.text_input("Phone"), st.text_input("Password (min 8)", type="password", key="sp")
            role = st.selectbox("I am a", ["user", "rider"])
            if st.form_submit_button("Create account"):
                if ok(call("POST", "/api/auth/register", {"name": name, "email": email, "phone": phone,
                                                          "password": pw, "role": role})):
                    st.success("Account created, now login.")
    with t3:
        em = st.text_input("Your email", key="fp")
        if st.button("Send reset link"):
            st.info(call("POST", "/api/auth/forgot-password", {"email": em})[1].get("message"))


# ------------------------------------------------------------ user pages
def page_restaurants():
    st.header("🍕 Restaurants")
    c1, c2 = st.columns(2)
    q, cat = c1.text_input("Search"), c2.text_input("Category (e.g. Pizza)")
    _, rs = call("GET", "/api/restaurants", params={"q": q, "category": cat})
    if not rs:
        st.info("No restaurants found.")
        return
    pick = st.selectbox("Open restaurant", rs, format_func=lambda r: f"{r['name']} ({r['category'] or '-'})")
    _, r = call("GET", f"/api/restaurants/{pick['id']}")
    st.subheader(r["name"])
    st.write(("🟢 Open" if r["is_open"] else "🔴 Closed") + f" · ⭐ {r['rating'] or 'new'} · "
             f"Delivery {money(r['delivery_fee'])} · Min order {money(r['min_order'])}")
    cats = sorted({i["category"] or "Other" for i in r["menu"]})
    for c in cats:
        st.markdown(f"#### {c}")
        for i in [m for m in r["menu"] if (m["category"] or "Other") == c]:
            with st.container(border=True):
                a, b = st.columns([1, 3])
                if i["image"]:
                    a.image(i["image"])
                b.markdown(f"**{i['name']}** — {money(i['price'])}")
                b.caption(i["description"] or "")
                if not i["available"]:
                    b.warning("Unavailable")
                    continue
                add = b.multiselect("Add-ons", i["addons"], format_func=lambda x: f"{x['name']} (+{money(x['price'])})",
                                    key=f"ad{i['id']}")
                qty = b.number_input("Qty", 1, 20, 1, key=f"q{i['id']}")
                if b.button("Add to cart 🛒", key=f"b{i['id']}"):
                    ok(call("POST", "/api/cart", {"item_id": i["id"], "qty": qty,
                                                  "addon_ids": [x["id"] for x in add]}), "Added to cart")
    if r["reviews"]:
        st.markdown("#### ⭐ Reviews")
        for v in r["reviews"]:
            st.write(f"{'⭐' * v['rating']} {v['comment'] or ''}")


def page_cart():
    st.header("🛒 Cart")
    _, cart = call("GET", "/api/cart")
    if not cart.get("items"):
        st.info("Your cart is empty.")
        return
    for it in cart["items"]:
        c = st.columns([4, 1, 1, 1, 1])
        c[0].write(f"**{it['name']}** {('+ ' + ', '.join(it['addons'])) if it['addons'] else ''}")
        c[1].write(f"x{it['qty']}")
        c[2].write(money(it["unit_price"] * it["qty"]))
        if c[3].button("➕", key=f"p{it['id']}"):
            call("PATCH", f"/api/cart/{it['id']}", {"qty": it["qty"] + 1})
            st.rerun()
        if c[4].button("➖", key=f"m{it['id']}"):
            call("PATCH", f"/api/cart/{it['id']}", {"qty": it["qty"] - 1})
            st.rerun()
        if c[4].button("🗑", key=f"d{it['id']}"):
            call("DELETE", f"/api/cart/{it['id']}")
            st.rerun()
    st.divider()
    st.write(f"Subtotal: {money(cart['subtotal'])} · Delivery: {money(cart['delivery_fee'])}")
    st.subheader(f"Total before discount: {money(cart['total'])}")
    _, addrs = call("GET", "/api/addresses")
    if not addrs:
        st.warning("Add a delivery address in Profile first.")
        return
    addr = st.selectbox("Deliver to", addrs, format_func=lambda a: f"{a['label'] or ''} {a['text']}")
    coupon = st.text_input("Coupon code")
    pay = st.radio("Payment", ["cod", "test"], format_func=lambda x: "Cash on Delivery" if x == "cod" else "Test card (fake)",
                   horizontal=True)
    if st.button("Place order", type="primary"):
        res = call("POST", "/api/orders", {"address_id": addr["id"], "coupon": coupon or None, "payment_method": pay})
        if ok(res):
            o = res[1]
            st.success(f"Order {o['public_id']} placed! Total {money(o['total'])} (discount {money(o['discount'])})")
            if pay == "test":
                st.info("Go to My Orders and press 'Pay now'.")


def page_orders():
    st.header("📦 My Orders")
    _, orders = call("GET", "/api/orders")
    if not orders:
        st.info("No orders yet.")
    for o in orders:
        with st.expander(f"{o['public_id']} · {STATUS_ICON.get(o['status'], '')} {o['status']} · {money(o['total'])}"):
            for l in o["items"]:
                st.write(f"{l['qty']} x {l['name']} {('(' + l['addons'] + ')') if l['addons'] else ''}")
            st.caption(f"{o['address']} · {o['payment_method']} / {o['payment_status']} · txn {o['transaction_id'] or '-'}")
            c = st.columns(3)
            if o["status"] in ("pending", "confirmed") and c[0].button("Cancel", key=f"c{o['id']}"):
                ok(call("POST", f"/api/orders/{o['id']}/cancel"))
                st.rerun()
            if c[1].button("Re-order", key=f"r{o['id']}"):
                ok(call("POST", f"/api/orders/{o['id']}/reorder"), "Items added to cart")
            if o["payment_method"] == "test" and o["payment_status"] == "unpaid" and o["status"] != "cancelled" \
                    and c[2].button("Pay now", key=f"pay{o['id']}"):
                ok(call("POST", f"/api/orders/{o['id']}/pay"), "Paid")
                st.rerun()
            if o["status"] == "delivered":
                with st.form(f"rv{o['id']}"):
                    target = st.selectbox("Review for", ["restaurant", "rider"])
                    rating, text = st.slider("Rating", 1, 5, 5), st.text_input("Comment")
                    if st.form_submit_button("Submit review"):
                        ok(call("POST", "/api/reviews", {"order_id": o["id"], "target": target, "rating": rating,
                                                         "comment": text}), "Thanks! Pending moderation.")


def page_profile():
    st.header("👤 Profile")
    _, me = call("GET", "/api/me")
    with st.form("prof"):
        name, phone = st.text_input("Name", me["name"]), st.text_input("Phone", me["phone"] or "")
        photo = st.text_input("Profile picture URL", me["photo"] or "")
        if st.form_submit_button("Save"):
            ok(call("PUT", "/api/me", {"name": name, "phone": phone, "photo": photo}), "Saved")
    if me["role"] != "user":
        return
    st.subheader("📍 Addresses")
    _, addrs = call("GET", "/api/addresses")
    for a in addrs:
        c = st.columns([5, 1])
        c[0].write(f"**{a['label'] or ''}** {a['text']}")
        if c[1].button("Delete", key=f"da{a['id']}"):
            call("DELETE", f"/api/addresses/{a['id']}")
            st.rerun()
    with st.form("addr"):
        label, text = st.text_input("Label (Home/Work)"), st.text_input("Full address")
        if st.form_submit_button("Add address") and ok(call("POST", "/api/addresses", {"label": label, "text": text})):
            st.rerun()
    st.subheader("💳 Payment history")
    _, pays = call("GET", "/api/payments")
    if pays:
        st.dataframe(pays, use_container_width=True)


# ------------------------------------------------------------ rider
def page_rider():
    st.header("🚴 Rider")
    _, d = call("GET", "/api/rider/orders")
    st.subheader("Available orders")
    for o in d.get("available", []):
        with st.container(border=True):
            st.write(f"**{o['public_id']}** → {o['address']} · {money(o['total'])}")
            if st.button("Accept", key=f"ac{o['id']}"):
                ok(call("POST", f"/api/rider/orders/{o['id']}/accept"))
                st.rerun()
    st.subheader("My deliveries")
    nxt = {"confirmed": None, "preparing": "picked_up", "picked_up": "out_for_delivery", "out_for_delivery": "delivered"}
    for o in d.get("mine", []):
        with st.container(border=True):
            st.write(f"**{o['public_id']}** {STATUS_ICON[o['status']]} {o['status']} → {o['address']}")
            c = st.columns(2)
            if nxt.get(o["status"]) and c[0].button(f"Mark {nxt[o['status']]}", key=f"st{o['id']}"):
                ok(call("POST", f"/api/rider/orders/{o['id']}/status", {"status": nxt[o["status"]]}))
                st.rerun()
            if c[1].button("Reject", key=f"rj{o['id']}"):
                ok(call("POST", f"/api/rider/orders/{o['id']}/reject"))
                st.rerun()
    st.subheader("History")
    _, h = call("GET", "/api/rider/history")
    st.dataframe([{"order": o["public_id"], "total": o["total"], "address": o["address"]} for o in h],
                 use_container_width=True)


# ------------------------------------------------------------ admin
def page_admin():
    st.header("👨‍💼 Admin")
    tabs = st.tabs(["Dashboard", "Orders", "Restaurants & Food", "Coupons", "Reviews", "Users"])
    with tabs[0]:
        _, s = call("GET", "/api/admin/dashboard")
        cols = st.columns(len(s) or 1)
        for c, (k, v) in zip(cols, s.items()):
            c.metric(k.title(), money(v) if k == "revenue" else v)
    with tabs[1]:
        _, orders = call("GET", "/api/admin/orders")
        for o in orders:
            c = st.columns([2, 2, 2, 3])
            c[0].write(o["public_id"])
            c[1].write(f"{STATUS_ICON[o['status']]} {o['status']}")
            c[2].write(money(o["total"]))
            act = {"pending": "confirmed", "confirmed": "preparing"}.get(o["status"])
            if act and c[3].button(f"→ {act}", key=f"ao{o['id']}"):
                ok(call("POST", f"/api/admin/orders/{o['id']}/status", {"status": act}))
                st.rerun()
    with tabs[2]:
        with st.form("nr"):
            st.subheader("New restaurant")
            n, cat = st.text_input("Name"), st.text_input("Category")
            img, fee, mn = st.text_input("Image URL"), st.number_input("Delivery fee", 0.0), st.number_input("Min order", 0.0)
            if st.form_submit_button("Create") and ok(call("POST", "/api/admin/restaurants", {
                    "name": n, "category": cat, "image": img, "delivery_fee": fee, "min_order": mn}), "Created"):
                st.rerun()
        _, rs = call("GET", "/api/restaurants")
        if rs:
            r = st.selectbox("Restaurant", rs, format_func=lambda x: x["name"], key="adm_r")
            if st.button("Toggle open/closed"):
                call("PATCH", f"/api/admin/restaurants/{r['id']}", {"is_open": not r["is_open"]})
                st.rerun()
            with st.form("ni"):
                st.subheader(f"New item for {r['name']}")
                n, cat = st.text_input("Item name"), st.text_input("Item category")
                desc, img, price = st.text_input("Description"), st.text_input("Image URL "), st.number_input("Price", 0.0)
                if st.form_submit_button("Add item") and ok(call("POST", "/api/admin/items", {
                        "restaurant_id": r["id"], "name": n, "category": cat, "description": desc,
                        "image": img, "price": price}), "Item added"):
                    st.rerun()
            _, full = call("GET", f"/api/restaurants/{r['id']}")
            if full["menu"]:
                it = st.selectbox("Item", full["menu"], format_func=lambda x: x["name"])
                with st.form("na"):
                    an, ap = st.text_input("Add-on name"), st.number_input("Add-on price", 0.0)
                    if st.form_submit_button("Add add-on"):
                        ok(call("POST", "/api/admin/addons", {"item_id": it["id"], "name": an, "price": ap}), "Added")
    with tabs[3]:
        with st.form("nc"):
            code, kind = st.text_input("Code"), st.selectbox("Type", ["percent", "fixed"])
            val, mn = st.number_input("Value", 0.0), st.number_input("Min order ", 0.0)
            lim, exp = st.number_input("Usage limit (0 = unlimited)", 0, step=1), st.date_input("Expiry date")
            if st.form_submit_button("Create coupon"):
                ok(call("POST", "/api/admin/coupons", {"code": code, "kind": kind, "value": val, "min_order": mn,
                                                       "usage_limit": lim or None,
                                                       "expires_at": f"{exp.isoformat()}T23:59:59"}), "Coupon created")
    with tabs[4]:
        _, rv = call("GET", "/api/admin/reviews")
        for v in rv:
            c = st.columns([5, 1, 1])
            c[0].write(f"{'⭐' * v['rating']} {v['comment'] or ''} {'(approved)' if v['approved'] else ''}")
            if not v["approved"] and c[1].button("Approve", key=f"ap{v['id']}"):
                call("POST", f"/api/admin/reviews/{v['id']}/approve")
                st.rerun()
            if c[2].button("Delete", key=f"rj{v['id']}"):
                call("POST", f"/api/admin/reviews/{v['id']}/reject")
                st.rerun()
    with tabs[5]:
        _, users = call("GET", "/api/admin/users")
        for u in users:
            c = st.columns([3, 3, 1, 2])
            c[0].write(u["name"])
            c[1].write(u["email"])
            c[2].write(u["role"])
            label = "Block" if u["is_active"] else "Unblock"
            if u["role"] != "admin" and c[3].button(label, key=f"u{u['id']}"):
                call("PATCH", f"/api/admin/users/{u['id']}", {"is_active": not u["is_active"]})
                st.rerun()


# ------------------------------------------------------------ router
if not st.session_state.get("token"):
    auth_screen()
    st.stop()

user = st.session_state.user
PAGES = {"user": {"🍕 Restaurants": page_restaurants, "🛒 Cart": page_cart, "📦 My Orders": page_orders,
                  "👤 Profile": page_profile},
         "rider": {"🚴 Deliveries": page_rider, "👤 Profile": page_profile},
         "admin": {"👨‍💼 Admin": page_admin}}[user["role"]]
with st.sidebar:
    st.write(f"Hi, **{user['name']}** ({user['role']})")
    choice = st.radio("Menu", list(PAGES))
    if st.button("Logout"):
        st.session_state.clear()
        st.rerun()
PAGES[choice]()
