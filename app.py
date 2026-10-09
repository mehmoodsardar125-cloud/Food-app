import os
from datetime import datetime, timedelta
from functools import wraps
from uuid import uuid4

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_jwt_extended import JWTManager, create_access_token, get_jwt_identity, verify_jwt_in_request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
from werkzeug.security import check_password_hash, generate_password_hash

load_dotenv()
app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL", "sqlite:///dev.db")
app.config["JWT_SECRET_KEY"] = os.getenv("JWT_SECRET_KEY")
if not app.config["JWT_SECRET_KEY"]:
    raise RuntimeError("JWT_SECRET_KEY environment variable is not set")
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(hours=12)
CORS(app, origins=os.getenv("CORS_ORIGINS", "http://localhost:5500").split(","))
db = SQLAlchemy(app)
JWTManager(app)
limiter = Limiter(get_remote_address, app=app, default_limits=["200 per minute"])

FLOW = ["pending", "confirmed", "preparing", "picked_up", "out_for_delivery", "delivered"]


# ---------------------------------------------------------------- models
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    phone = db.Column(db.String(20))
    photo = db.Column(db.String(300))
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(10), default="user")  # user | rider | admin
    is_active = db.Column(db.Boolean, default=True)

    def to_dict(self):
        return {k: getattr(self, k) for k in ("id", "name", "email", "phone", "photo", "role")}


class Address(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.ForeignKey("user.id"), nullable=False)
    label = db.Column(db.String(30))
    text = db.Column(db.String(300), nullable=False)


class Restaurant(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    category = db.Column(db.String(50))
    image = db.Column(db.String(300))
    is_open = db.Column(db.Boolean, default=True)
    delivery_fee = db.Column(db.Float, default=0)
    min_order = db.Column(db.Float, default=0)
    items = db.relationship("MenuItem", backref="restaurant")

    def to_dict(self):
        avg = db.session.query(func.avg(Review.rating)).filter_by(restaurant_id=self.id, approved=True).scalar()
        d = {k: getattr(self, k) for k in ("id", "name", "category", "image", "is_open", "delivery_fee", "min_order")}
        d["rating"] = round(avg, 1) if avg else None
        return d


class MenuItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    restaurant_id = db.Column(db.ForeignKey("restaurant.id"), nullable=False)
    category = db.Column(db.String(50))
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(300))
    price = db.Column(db.Float, nullable=False)
    image = db.Column(db.String(300))
    available = db.Column(db.Boolean, default=True)
    addons = db.relationship("AddOn", backref="item")

    def to_dict(self):
        d = {k: getattr(self, k) for k in ("id", "restaurant_id", "category", "name", "description", "price", "image", "available")}
        d["addons"] = [{"id": a.id, "name": a.name, "price": a.price} for a in self.addons]
        return d


class AddOn(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.ForeignKey("menu_item.id"), nullable=False)
    name = db.Column(db.String(50), nullable=False)
    price = db.Column(db.Float, default=0)


class CartItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.ForeignKey("user.id"), nullable=False)
    item_id = db.Column(db.ForeignKey("menu_item.id"), nullable=False)
    qty = db.Column(db.Integer, default=1)
    addon_ids = db.Column(db.String(100), default="")  # "1,4"
    item = db.relationship("MenuItem")

    def addons(self):
        ids = [int(i) for i in self.addon_ids.split(",") if i]
        return AddOn.query.filter(AddOn.id.in_(ids), AddOn.item_id == self.item_id).all() if ids else []

    def unit_price(self):
        return self.item.price + sum(a.price for a in self.addons())


class Coupon(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(30), unique=True, nullable=False)
    kind = db.Column(db.String(10), nullable=False)  # percent | fixed
    value = db.Column(db.Float, nullable=False)
    expires_at = db.Column(db.DateTime)
    min_order = db.Column(db.Float, default=0)
    usage_limit = db.Column(db.Integer)
    used_count = db.Column(db.Integer, default=0)


class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(12), unique=True, default=lambda: uuid4().hex[:10].upper())
    user_id = db.Column(db.ForeignKey("user.id"), nullable=False)
    restaurant_id = db.Column(db.ForeignKey("restaurant.id"), nullable=False)
    rider_id = db.Column(db.ForeignKey("user.id"))
    address = db.Column(db.String(300), nullable=False)
    status = db.Column(db.String(20), default="pending")
    subtotal = db.Column(db.Float)
    delivery_fee = db.Column(db.Float)
    discount = db.Column(db.Float, default=0)
    total = db.Column(db.Float)
    coupon_code = db.Column(db.String(30))
    payment_method = db.Column(db.String(10), default="cod")  # cod | test
    payment_status = db.Column(db.String(10), default="unpaid")
    transaction_id = db.Column(db.String(40))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    lines = db.relationship("OrderLine", backref="order")

    def to_dict(self):
        d = {k: getattr(self, k) for k in ("public_id", "restaurant_id", "rider_id", "address", "status", "subtotal",
             "delivery_fee", "discount", "total", "coupon_code", "payment_method", "payment_status", "transaction_id")}
        d["id"] = self.id
        d["created_at"] = self.created_at.isoformat()
        d["items"] = [{"name": l.name, "price": l.price, "qty": l.qty, "addons": l.addons} for l in self.lines]
        return d


class OrderLine(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.ForeignKey("order.id"), nullable=False)
    item_id = db.Column(db.Integer)
    name = db.Column(db.String(100))
    price = db.Column(db.Float)  # unit price incl. add-ons (snapshot)
    qty = db.Column(db.Integer)
    addons = db.Column(db.String(200), default="")


class Review(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.ForeignKey("user.id"), nullable=False)
    order_id = db.Column(db.ForeignKey("order.id"), nullable=False)
    restaurant_id = db.Column(db.Integer)
    item_id = db.Column(db.Integer)
    rider_id = db.Column(db.Integer)
    rating = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.String(500))
    approved = db.Column(db.Boolean, default=False)  # moderation


# ---------------------------------------------------------------- helpers
def current_user():
    return db.session.get(User, int(get_jwt_identity()))


def roles(*allowed):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            verify_jwt_in_request()
            u = current_user()
            if not u or not u.is_active or u.role not in allowed:
                return jsonify(error="forbidden"), 403
            return fn(*a, **kw)
        return wrapper
    return deco


def need(data, *fields):
    missing = [f for f in fields if data.get(f) in (None, "")]
    if missing:
        return jsonify(error=f"missing: {', '.join(missing)}"), 400


def apply_coupon(code, subtotal):
    if not code:
        return 0, None
    c = Coupon.query.filter_by(code=code.upper()).first()
    if not c:
        raise ValueError("invalid coupon")
    if c.expires_at and c.expires_at < datetime.utcnow():
        raise ValueError("coupon expired")
    if subtotal < (c.min_order or 0):
        raise ValueError(f"coupon needs minimum order {c.min_order}")
    if c.usage_limit is not None and c.used_count >= c.usage_limit:
        raise ValueError("coupon usage limit reached")
    off = subtotal * c.value / 100 if c.kind == "percent" else c.value
    return round(min(off, subtotal), 2), c


@app.get("/")
def index():
    return jsonify(service="Foodeliver API", status="ok", ui="http://localhost:8501")


# ---------------------------------------------------------------- auth + profile
@app.post("/api/auth/register")
@limiter.limit("10 per hour")
def register():
    d = request.get_json(force=True)
    if (e := need(d, "name", "email", "password")):
        return e
    if len(d["password"]) < 8:
        return jsonify(error="password must be at least 8 chars"), 400
    if User.query.filter_by(email=d["email"].lower()).first():
        return jsonify(error="email already used"), 409
    role = d.get("role", "user")
    if role not in ("user", "rider"):  # admins only via CLI
        return jsonify(error="invalid role"), 400
    u = User(name=d["name"], email=d["email"].lower(), phone=d.get("phone"),
             password_hash=generate_password_hash(d["password"]), role=role)
    db.session.add(u)
    db.session.commit()
    return jsonify(u.to_dict()), 201


@app.post("/api/auth/login")
@limiter.limit("10 per minute")
def login():
    d = request.get_json(force=True)
    u = User.query.filter_by(email=(d.get("email") or "").lower()).first()
    if not u or not u.is_active or not check_password_hash(u.password_hash, d.get("password") or ""):
        return jsonify(error="invalid credentials"), 401
    return jsonify(token=create_access_token(identity=str(u.id)), user=u.to_dict())


@app.post("/api/auth/forgot-password")
@limiter.limit("5 per hour")
def forgot_password():
    # TODO: generate a reset token and email it (SMTP/SendGrid). Always same response.
    return jsonify(message="If the email exists, a reset link has been sent.")


@app.route("/api/me", methods=["GET", "PUT"])
@roles("user", "rider", "admin")
def me():
    u = current_user()
    if request.method == "PUT":
        d = request.get_json(force=True)
        for f in ("name", "phone", "photo"):
            if f in d:
                setattr(u, f, d[f])
        db.session.commit()
    return jsonify(u.to_dict())


@app.route("/api/addresses", methods=["GET", "POST"])
@roles("user")
def addresses():
    u = current_user()
    if request.method == "POST":
        d = request.get_json(force=True)
        if (e := need(d, "text")):
            return e
        db.session.add(Address(user_id=u.id, label=d.get("label"), text=d["text"]))
        db.session.commit()
    return jsonify([{"id": a.id, "label": a.label, "text": a.text} for a in Address.query.filter_by(user_id=u.id)])


@app.delete("/api/addresses/<int:aid>")
@roles("user")
def del_address(aid):
    Address.query.filter_by(id=aid, user_id=current_user().id).delete()
    db.session.commit()
    return "", 204


# ---------------------------------------------------------------- restaurants + menu
@app.get("/api/restaurants")
def list_restaurants():
    q = Restaurant.query
    if s := request.args.get("q"):
        q = q.filter(Restaurant.name.ilike(f"%{s}%"))
    if c := request.args.get("category"):
        q = q.filter_by(category=c)
    return jsonify([r.to_dict() for r in q.all()])


@app.get("/api/restaurants/<int:rid>")
def restaurant(rid):
    r = db.get_or_404(Restaurant, rid)
    reviews = Review.query.filter_by(restaurant_id=rid, approved=True).limit(20).all()
    return jsonify(**r.to_dict(), menu=[i.to_dict() for i in r.items],
                   reviews=[{"rating": v.rating, "comment": v.comment} for v in reviews])


# ---------------------------------------------------------------- cart
def cart_view(uid):
    rows = CartItem.query.filter_by(user_id=uid).all()
    subtotal = round(sum(r.unit_price() * r.qty for r in rows), 2)
    fee = rows[0].item.restaurant.delivery_fee if rows else 0
    return {"items": [{"id": r.id, "name": r.item.name, "qty": r.qty, "unit_price": r.unit_price(),
                       "addons": [a.name for a in r.addons()]} for r in rows],
            "subtotal": subtotal, "delivery_fee": fee, "total": round(subtotal + fee, 2)}


@app.get("/api/cart")
@roles("user")
def get_cart():
    return jsonify(cart_view(current_user().id))


@app.post("/api/cart")
@roles("user")
def add_cart():
    u, d = current_user(), request.get_json(force=True)
    if (e := need(d, "item_id")):
        return e
    item = db.get_or_404(MenuItem, d["item_id"])
    if not item.available or not item.restaurant.is_open:
        return jsonify(error="item unavailable or restaurant closed"), 400
    existing = CartItem.query.filter_by(user_id=u.id).first()
    if existing and existing.item.restaurant_id != item.restaurant_id:
        return jsonify(error="cart has items from another restaurant"), 400
    qty = max(1, int(d.get("qty", 1)))
    db.session.add(CartItem(user_id=u.id, item_id=item.id, qty=qty,
                            addon_ids=",".join(str(i) for i in d.get("addon_ids", []))))
    db.session.commit()
    return jsonify(cart_view(u.id)), 201


@app.patch("/api/cart/<int:cid>")
@roles("user")
def update_cart(cid):
    u = current_user()
    row = CartItem.query.filter_by(id=cid, user_id=u.id).first_or_404()
    row.qty = int(request.get_json(force=True).get("qty", row.qty))
    if row.qty < 1:
        db.session.delete(row)
    db.session.commit()
    return jsonify(cart_view(u.id))


@app.delete("/api/cart/<int:cid>")
@roles("user")
def remove_cart(cid):
    u = current_user()
    CartItem.query.filter_by(id=cid, user_id=u.id).delete()
    db.session.commit()
    return jsonify(cart_view(u.id))


# ---------------------------------------------------------------- orders
def create_order(u, rows, address, coupon_code, method):
    r = rows[0].item.restaurant
    if not r.is_open:
        raise ValueError("restaurant is closed")
    subtotal = round(sum(x.unit_price() * x.qty for x in rows), 2)
    if subtotal < r.min_order:
        raise ValueError(f"minimum order is {r.min_order}")
    discount, coupon = apply_coupon(coupon_code, subtotal)
    o = Order(user_id=u.id, restaurant_id=r.id, address=address, subtotal=subtotal, delivery_fee=r.delivery_fee,
              discount=discount, total=round(subtotal + r.delivery_fee - discount, 2),
              coupon_code=coupon.code if coupon else None, payment_method=method)
    if coupon:
        coupon.used_count += 1
    db.session.add(o)
    db.session.flush()
    for x in rows:
        db.session.add(OrderLine(order_id=o.id, item_id=x.item_id, name=x.item.name, price=x.unit_price(),
                                 qty=x.qty, addons=", ".join(a.name for a in x.addons())))
    return o


@app.post("/api/orders")
@roles("user")
def place_order():
    u, d = current_user(), request.get_json(force=True)
    rows = CartItem.query.filter_by(user_id=u.id).all()
    if not rows:
        return jsonify(error="cart is empty"), 400
    addr = Address.query.filter_by(id=d.get("address_id"), user_id=u.id).first()
    if not addr:
        return jsonify(error="valid address_id required"), 400
    method = d.get("payment_method", "cod")
    if method not in ("cod", "test"):
        return jsonify(error="payment_method must be cod or test"), 400
    try:
        o = create_order(u, rows, addr.text, d.get("coupon"), method)
    except ValueError as e:
        db.session.rollback()
        return jsonify(error=str(e)), 400
    CartItem.query.filter_by(user_id=u.id).delete()
    db.session.commit()
    return jsonify(o.to_dict()), 201


@app.get("/api/orders")
@roles("user")
def order_history():
    os_ = Order.query.filter_by(user_id=current_user().id).order_by(Order.id.desc()).all()
    return jsonify([o.to_dict() for o in os_])


@app.get("/api/orders/<int:oid>")
@roles("user")
def order_detail(oid):
    return jsonify(Order.query.filter_by(id=oid, user_id=current_user().id).first_or_404().to_dict())


@app.post("/api/orders/<int:oid>/cancel")
@roles("user")
def cancel_order(oid):
    o = Order.query.filter_by(id=oid, user_id=current_user().id).first_or_404()
    if o.status not in ("pending", "confirmed"):
        return jsonify(error="order can no longer be cancelled"), 400
    o.status = "cancelled"
    db.session.commit()
    return jsonify(o.to_dict())


@app.post("/api/orders/<int:oid>/reorder")
@roles("user")
def reorder(oid):
    u = current_user()
    o = Order.query.filter_by(id=oid, user_id=u.id).first_or_404()
    CartItem.query.filter_by(user_id=u.id).delete()
    for l in o.lines:
        item = db.session.get(MenuItem, l.item_id)
        if item and item.available:
            db.session.add(CartItem(user_id=u.id, item_id=item.id, qty=l.qty))
    db.session.commit()
    return jsonify(cart_view(u.id))


@app.post("/api/orders/<int:oid>/pay")
@roles("user")
def fake_pay(oid):
    """Test gateway: always succeeds. Replace with Stripe/JazzCash/Easypaisa later."""
    o = Order.query.filter_by(id=oid, user_id=current_user().id).first_or_404()
    if o.payment_method != "test" or o.payment_status == "paid":
        return jsonify(error="nothing to pay"), 400
    o.payment_status, o.transaction_id = "paid", "TEST-" + uuid4().hex[:12].upper()
    db.session.commit()
    return jsonify(o.to_dict())


@app.get("/api/payments")
@roles("user")
def payment_history():
    os_ = Order.query.filter_by(user_id=current_user().id).order_by(Order.id.desc()).all()
    return jsonify([{"order": o.public_id, "amount": o.total, "method": o.payment_method,
                     "status": o.payment_status, "transaction_id": o.transaction_id} for o in os_])


# ---------------------------------------------------------------- reviews
@app.post("/api/reviews")
@roles("user")
def add_review():
    u, d = current_user(), request.get_json(force=True)
    o = Order.query.filter_by(id=d.get("order_id"), user_id=u.id, status="delivered").first()
    if not o:
        return jsonify(error="you can only review delivered orders"), 400
    if not 1 <= int(d.get("rating", 0)) <= 5:
        return jsonify(error="rating must be 1-5"), 400
    target = d.get("target", "restaurant")  # restaurant | food | rider
    rv = Review(user_id=u.id, order_id=o.id, rating=int(d["rating"]), comment=d.get("comment"))
    if target == "restaurant":
        rv.restaurant_id = o.restaurant_id
    elif target == "rider":
        rv.rider_id = o.rider_id
    else:
        rv.item_id = d.get("item_id")
    db.session.add(rv)
    db.session.commit()
    return jsonify(message="review submitted, pending moderation"), 201


# ---------------------------------------------------------------- rider
@app.get("/api/rider/orders")
@roles("rider")
def rider_orders():
    uid = current_user().id
    mine = Order.query.filter_by(rider_id=uid).filter(Order.status.in_(FLOW[3:5] + ["confirmed", "preparing"])).all()
    avail = Order.query.filter_by(rider_id=None).filter(Order.status.in_(["confirmed", "preparing"])).all()
    return jsonify(mine=[o.to_dict() for o in mine], available=[o.to_dict() for o in avail])


@app.post("/api/rider/orders/<int:oid>/accept")
@roles("rider")
def rider_accept(oid):
    o = Order.query.filter_by(id=oid, rider_id=None).filter(Order.status.in_(["confirmed", "preparing"])).first_or_404()
    o.rider_id = current_user().id
    db.session.commit()
    return jsonify(o.to_dict())


@app.post("/api/rider/orders/<int:oid>/reject")
@roles("rider")
def rider_reject(oid):
    o = Order.query.filter_by(id=oid, rider_id=current_user().id).first_or_404()
    if o.status in ("picked_up", "out_for_delivery", "delivered"):
        return jsonify(error="cannot reject now"), 400
    o.rider_id = None
    db.session.commit()
    return jsonify(o.to_dict())


@app.post("/api/rider/orders/<int:oid>/status")
@roles("rider")
def rider_status(oid):
    o = Order.query.filter_by(id=oid, rider_id=current_user().id).first_or_404()
    new = request.get_json(force=True).get("status")
    if new not in FLOW[3:] or FLOW.index(new) != FLOW.index(o.status) + 1:
        return jsonify(error="invalid status transition"), 400
    o.status = new
    if new == "delivered" and o.payment_method == "cod":
        o.payment_status = "paid"
    db.session.commit()
    return jsonify(o.to_dict())


@app.get("/api/rider/history")
@roles("rider")
def rider_history():
    os_ = Order.query.filter_by(rider_id=current_user().id, status="delivered").all()
    return jsonify([o.to_dict() for o in os_])


# ---------------------------------------------------------------- admin
@app.get("/api/admin/dashboard")
@roles("admin")
def dashboard():
    done = Order.query.filter_by(status="delivered")
    return jsonify(users=User.query.filter_by(role="user").count(), riders=User.query.filter_by(role="rider").count(),
                   restaurants=Restaurant.query.count(), orders=Order.query.count(),
                   delivered=done.count(), revenue=round(sum(o.total for o in done), 2))


@app.get("/api/admin/users")
@roles("admin")
def admin_users():
    return jsonify([dict(u.to_dict(), is_active=u.is_active) for u in User.query.all()])


@app.patch("/api/admin/users/<int:uid>")
@roles("admin")
def admin_user(uid):
    u = db.get_or_404(User, uid)
    u.is_active = bool(request.get_json(force=True).get("is_active", u.is_active))
    db.session.commit()
    return jsonify(u.to_dict())


def crud_create(model, fields):
    d = request.get_json(force=True)
    obj = model(**{f: d[f] for f in fields if f in d})
    db.session.add(obj)
    db.session.commit()
    return jsonify(id=obj.id), 201


@app.post("/api/admin/restaurants")
@roles("admin")
def admin_add_restaurant():
    return crud_create(Restaurant, ["name", "category", "image", "is_open", "delivery_fee", "min_order"])


@app.patch("/api/admin/restaurants/<int:rid>")
@roles("admin")
def admin_edit_restaurant(rid):
    r = db.get_or_404(Restaurant, rid)
    for k, v in request.get_json(force=True).items():
        if k in ("name", "category", "image", "is_open", "delivery_fee", "min_order"):
            setattr(r, k, v)
    db.session.commit()
    return jsonify(r.to_dict())


@app.post("/api/admin/items")
@roles("admin")
def admin_add_item():
    return crud_create(MenuItem, ["restaurant_id", "category", "name", "description", "price", "image", "available"])


@app.post("/api/admin/addons")
@roles("admin")
def admin_add_addon():
    return crud_create(AddOn, ["item_id", "name", "price"])


@app.post("/api/admin/coupons")
@roles("admin")
def admin_add_coupon():
    d = request.get_json(force=True)
    if d.get("kind") not in ("percent", "fixed"):
        return jsonify(error="kind must be percent or fixed"), 400
    c = Coupon(code=d["code"].upper(), kind=d["kind"], value=d["value"], min_order=d.get("min_order", 0),
               usage_limit=d.get("usage_limit"),
               expires_at=datetime.fromisoformat(d["expires_at"]) if d.get("expires_at") else None)
    db.session.add(c)
    db.session.commit()
    return jsonify(id=c.id), 201


@app.get("/api/admin/orders")
@roles("admin")
def admin_orders():
    return jsonify([o.to_dict() for o in Order.query.order_by(Order.id.desc()).limit(200)])


@app.post("/api/admin/orders/<int:oid>/status")
@roles("admin")
def admin_order_status(oid):
    o = db.get_or_404(Order, oid)
    new = request.get_json(force=True).get("status")
    if new not in ("confirmed", "preparing", "cancelled"):
        return jsonify(error="admin can set confirmed, preparing or cancelled"), 400
    o.status = new
    db.session.commit()
    return jsonify(o.to_dict())


@app.get("/api/admin/reviews")
@roles("admin")
def admin_reviews():
    return jsonify([{"id": r.id, "rating": r.rating, "comment": r.comment, "approved": r.approved}
                    for r in Review.query.order_by(Review.id.desc())])


@app.post("/api/admin/reviews/<int:rid>/<action>")
@roles("admin")
def moderate(rid, action):
    r = db.get_or_404(Review, rid)
    if action == "approve":
        r.approved = True
    elif action == "reject":
        db.session.delete(r)
    else:
        return jsonify(error="unknown action"), 400
    db.session.commit()
    return "", 204


# ---------------------------------------------------------------- CLI
@app.cli.command("init-db")
def init_db():
    db.create_all()
    print("tables created")


@app.cli.command("create-admin")
def create_admin():
    email, pw = os.environ["ADMIN_EMAIL"], os.environ["ADMIN_PASSWORD"]
    db.session.add(User(name="Admin", email=email, password_hash=generate_password_hash(pw), role="admin"))
    db.session.commit()
    print("admin created:", email)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

    app.config["JWT_SECRET_KEY"] = os.getenv("JWT_SECRET_KEY")
import os
if not app.config["JWT_SECRET_KEY"]:
    raise RuntimeError("JWT_SECRET_KEY environment variable is not set")