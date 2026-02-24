import razorpay
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from app_v2.core.config import VoiceSettings
from app_v2.schemas.enum_types import BillingPeriodEnum

class BasePaymentProvider(ABC):
    @abstractmethod
    def create_plan(self, name: str, amount: float, currency: str, period: BillingPeriodEnum, interval: int = 1, description: Optional[str] = None) -> Dict[str, Any]:
        pass

    @abstractmethod
    def create_subscription(self, plan_id: str, total_count: int = 12, quantity: int = 1, start_at: Optional[int] = None, expire_by: Optional[int] = None, notes: Optional[Dict] = None) -> Dict[str, Any]:
        pass

    @abstractmethod
    def verify_payment_signature(self, params: Dict[str, str]) -> bool:
        pass

class RazorpayProvider(BasePaymentProvider):
    def __init__(self):
        self.client = razorpay.Client(auth=(VoiceSettings.RAZOR_KEY_ID, VoiceSettings.RAZOR_KEY_SECRET))

    def create_plan(self, name: str, amount: float, currency: str, period: BillingPeriodEnum, interval: int = 1, description: Optional[str] = None) -> Dict[str, Any]:
        # Razorpay expects amount in smallest currency unit (paisa for INR)
        amount_in_units = int(amount * 100)
        
        # Map BillingPeriodEnum to Razorpay period
        rzp_period = "monthly"
        if period == BillingPeriodEnum.annual:
            rzp_period = "yearly"
        elif period == BillingPeriodEnum.monthly:
            rzp_period = "monthly"
            
        data = {
            "period": rzp_period,
            "interval": interval,
            "item": {
                "name": name,
                "amount": amount_in_units,
                "currency": currency,
                "description": description or name
            }
        }
        
        try:
            plan = self.client.plan.create(data=data)
            return {
                "provider_plan_id": plan["id"],
                "provider_metadata": plan
            }
        except Exception as e:
            raise Exception(f"Razorpay plan creation failed: {str(e)}")

    def create_subscription(self, plan_id: str, total_count: int = 12, quantity: int = 1, start_at: Optional[int] = None, expire_by: Optional[int] = None, notes: Optional[Dict] = None) -> Dict[str, Any]:
        data = {
            "plan_id": plan_id,
            "total_count": total_count,
            "quantity": quantity,
            "customer_notify": 1,
        }
        if start_at:
            data["start_at"] = start_at
        if expire_by:
            data["expire_by"] = expire_by
        if notes:
            data["notes"] = notes

        try:
            subscription = self.client.subscription.create(data=data)
            return subscription
        except Exception as e:
            raise Exception(f"Razorpay subscription creation failed: {str(e)}")

    def verify_payment_signature(self, params: Dict[str, str]) -> bool:
        try:
            self.client.utility.verify_payment_signature(params)
            return True
        except Exception:
            return False

class PaymentProviderFactory:
    @staticmethod
    def get_provider(provider_name: str) -> BasePaymentProvider:
        if provider_name.lower() == "razorpay":
            return RazorpayProvider()
        elif provider_name.lower() == "stripe":
            # StripeProvider would be implemented here later
            raise NotImplementedError("Stripe provider is not implemented yet.")
        else:
            raise ValueError(f"Unknown payment provider: {provider_name}")
