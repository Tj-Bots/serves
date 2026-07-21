import secrets

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth import get_current_verified_user
from app.config import PLANS, settings
from app.database import get_db
from app.models import PlanPurchase, PurchaseStatus, User
from app.web_utils import render

router = APIRouter()


@router.get("/billing")
def billing_page(request: Request, user: User = Depends(get_current_verified_user)):
    return render(
        request,
        "billing.html",
        user=user,
        plans=PLANS,
        payments_enabled=bool(settings.PAYMENT_BOT_USERNAME),
    )


@router.post("/billing/upgrade/{plan_name}")
def start_upgrade(
    plan_name: str,
    user: User = Depends(get_current_verified_user),
    db: Session = Depends(get_db),
):
    plan = PLANS.get(plan_name)
    if not plan or plan_name == "free" or not settings.PAYMENT_BOT_USERNAME:
        return RedirectResponse("/billing", status_code=303)

    purchase = PlanPurchase(
        user_id=user.id,
        pay_code=secrets.token_urlsafe(16),
        plan_name=plan_name,
        stars_amount=plan["stars"],
    )
    db.add(purchase)
    db.commit()
    return RedirectResponse(f"/billing/pay/{purchase.pay_code}", status_code=303)


@router.get("/billing/pay/{pay_code}")
def pay_page(
    request: Request,
    pay_code: str,
    user: User = Depends(get_current_verified_user),
    db: Session = Depends(get_db),
):
    purchase = db.query(PlanPurchase).filter(PlanPurchase.pay_code == pay_code, PlanPurchase.user_id == user.id).first()
    if not purchase:
        return RedirectResponse("/billing", status_code=303)
    if purchase.status == PurchaseStatus.PAID:
        return RedirectResponse("/dashboard", status_code=303)

    deep_link = f"https://t.me/{settings.PAYMENT_BOT_USERNAME}?start=pay_{purchase.pay_code}"
    return render(request, "billing_pay.html", user=user, purchase=purchase, deep_link=deep_link)


@router.get("/billing/status/{pay_code}")
def pay_status(
    pay_code: str,
    user: User = Depends(get_current_verified_user),
    db: Session = Depends(get_db),
):
    purchase = db.query(PlanPurchase).filter(PlanPurchase.pay_code == pay_code, PlanPurchase.user_id == user.id).first()
    if not purchase:
        return JSONResponse({"status": "not_found"}, status_code=404)
    return JSONResponse({"status": purchase.status.value})
