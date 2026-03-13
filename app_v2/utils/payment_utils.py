import razorpay
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from app_v2.core.config import VoiceSettings
from app_v2.schemas.enum_types import BillingPeriodEnum
import requests
from app_v2.core.logger import setup_logger
from requests.auth import HTTPBasicAuth
from app_v2.schemas.enum_types import BillingPeriodEnum
from typing import List, Dict, Any
import hmac
import hashlib
logger = setup_logger(__name__)
import datetime



class BasePaymentProvider(ABC):
    @abstractmethod
    def create_plan(self, name: str, amount: float, currency: str, period: BillingPeriodEnum, interval: int = 1, description: Optional[str] = None) -> Dict[str, Any]:
        pass

    @abstractmethod
    def create_subscription(self, plan_id: str, total_count: int = 12, quantity: int = 1, start_at: Optional[int] = None, expire_by: Optional[int] = None, notes: Optional[Dict] = None, customer_id: Optional[str] = None) -> Dict[str, Any]:
        pass

    @abstractmethod
    def verify_payment_signature(self, params: Dict[str, str]) -> bool:
        pass

    @abstractmethod
    def create_order(self, amount: float, currency: str, receipt: str, notes: Optional[Dict] = None) -> Dict[str, Any]:
        pass

    @abstractmethod
    def verify_order_signature(self, params: Dict[str, str]) -> bool:
        pass

    @abstractmethod
    def cancel_subscription(self, subscription_id: str, cancel_at_cycle_end: bool = True) -> Dict[str, Any]:
        pass

    @abstractmethod
    def update_subscription(self, subscription_id: str, plan_id: str, billing_period: BillingPeriodEnum, offer_id: Optional[str] = None, start_at: Optional[int] = None) -> Dict[str, Any]:
        pass

    @abstractmethod
    def pause_subscription(self, subscription_id: str, pause_at: str = "now") -> Dict[str, Any]:
        pass

    @abstractmethod
    def resume_subscription(self, subscription_id: str, resume_at: str = "now") -> Dict[str, Any]:
        pass

    @abstractmethod
    def get_subscription_invoices(self, subscription_id: str) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    def get_order_invoices(self, order_id: str) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    def get_subscription_details(self, subscription_id: str) -> Dict[str, Any]:
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

    def create_subscription(self, plan_id: str, total_count: int = 12, quantity: int = 1, start_at: Optional[int] = None, expire_by: Optional[int] = None, notes: Optional[Dict] = None, customer_id: Optional[str] = None) -> Dict[str, Any]:
        data = {
            "plan_id": plan_id,
            "total_count": total_count,
            "quantity": quantity,
            "customer_notify": 1,
        }
        if customer_id:
            data["customer_id"] = customer_id
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
            subscription_id = params.get("razorpay_subscription_id")
            payment_id = params.get("razorpay_payment_id")
            razorpay_signature = params.get("razorpay_signature")
            body = payment_id+"|"+subscription_id
            generated_signature = hmac.new(
                VoiceSettings.RAZOR_KEY_SECRET.encode(),
                body.encode(),
                hashlib.sha256
            ).hexdigest()
            logger.info(f"Generated signature: {generated_signature}")
            logger.info(f"Razorpay signature: {razorpay_signature}")
            return hmac.compare_digest(generated_signature, razorpay_signature)

        except Exception:
            return False

    def create_order(self, amount: float, currency: str, receipt: str, notes: Optional[Dict] = None) -> Dict[str, Any]:
        # Razorpay expects amount in smallest currency unit (paisa for INR)
        amount_in_units = int(amount * 100)
        data = {
            "amount": amount_in_units,
            "currency": currency,
            "receipt": receipt,
            "notes": notes or {}
        }
        try:
            order = self.client.order.create(data=data)
            return order
        except Exception as e:
            raise Exception(f"Razorpay order creation failed: {str(e)}")

    def verify_order_signature(self, params: Dict[str, str]) -> bool:
        try:
            order_id = params.get("razorpay_order_id")
            payment_id = params.get("razorpay_payment_id")
            razorpay_signature = params.get("razorpay_signature")
            
            # For orders, the signature is based on order_id + "|" + payment_id
            return self.client.utility.verify_payment_signature({
                'razorpay_order_id': order_id,
                'razorpay_payment_id': payment_id,
                'razorpay_signature': razorpay_signature
            })
        except Exception as e:
            logger.error(f"Razorpay order signature verification failed: {str(e)}")
            return False

    def cancel_subscription(self, subscription_id: str, cancel_at_cycle_end: bool = True) -> Dict[str, Any]:
        try:
            # cancel_at_cycle_end: 1 for end of cycle, 0 for immediate
            cancel_at = 1 if cancel_at_cycle_end else 0
            response = self.client.subscription.cancel(subscription_id, {"cancel_at_cycle_end": cancel_at})
            return response
        except Exception as e:
            logger.error(f"Razorpay subscription cancellation failed: {str(e)}")
            raise Exception(f"Failed to cancel subscription: {str(e)}")

    def update_subscription(
        self,
        subscription_id: str,
        new_plan_id: str,
        billing_period: BillingPeriodEnum,
        offer_id: Optional[str] = None,
        start_at: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Plan change flow:
          1. Cancel the existing subscription at cycle end.
          2. Create a new subscription on the new plan.

        FIX 4: If step 1 succeeds but step 2 fails we log a CRITICAL alert
        with the cancelled subscription_id.  Razorpay cancellations cannot be
        rolled back via API, so ops must manually re-create the subscription.
        The caller (router) receives a clear exception message indicating which
        stage failed.
        """
        logger.info(f"update_subscription | cancelling {subscription_id}")

        # Step 1 – cancel existing
        try:
            cancel_response = self.client.subscription.cancel(
                subscription_id, {"cancel_at_cycle_end": True}
            )
        except Exception as e:
            logger.error(f"update_subscription: cancel step failed | sub={subscription_id} | {e}")
            raise Exception(f"Subscription cancel step failed: {e}")

        logger.info(f"update_subscription | cancelled {subscription_id} | creating new sub on plan {new_plan_id}")

        # Step 2 – create new subscription
        payload: Dict[str, Any] = {"plan_id": new_plan_id}

        customer_id = cancel_response.get("customer_id")
        if customer_id:
            payload["customer_id"] = customer_id

        payload["total_count"] = 1 if billing_period == BillingPeriodEnum.annual else 12

        if start_at:
            payload["start_at"] = int(start_at.timestamp())

        if offer_id:
            payload["offer_id"] = offer_id

        try:
            new_subscription = self.client.subscription.create(payload)
        except Exception as e:
            # FIX 4: CRITICAL – old sub is already cancelled, new one failed
            logger.critical(
                f"update_subscription: NEW SUBSCRIPTION CREATION FAILED after cancelling "
                f"{subscription_id}. Manual intervention required. "
                f"customer_id={customer_id} new_plan_id={new_plan_id} error={e}"
            )
            raise Exception(
                f"Subscription was cancelled but new subscription creation failed: {e}. "
                f"Please contact support – your previous subscription ID was {subscription_id}."
            )

        logger.info(f"update_subscription | new sub created: {new_subscription['id']}")

        return {
            "cancelled_subscription": cancel_response,
            "new_subscription": new_subscription,
        }

    def pause_subscription(self, subscription_id: str, pause_at: str = "now") -> Dict[str, Any]:
        try:
            return self.client.subscription.pause(subscription_id, {"pause_at": pause_at})
        except Exception as e:
            logger.error(f"Razorpay subscription pause failed: {e}")
            raise Exception(f"Failed to pause subscription: {e}")
    
    def pause_subscription(self, subscription_id: str, pause_at: str = "now") -> Dict[str, Any]:
            try:
                # pause_at can be 'now' or a timestamp
                response = self.client.subscription.pause(subscription_id, {"pause_at": pause_at})
                return response
            except Exception as e:
                logger.error(f"Razorpay subscription pause failed: {str(e)}")
                raise Exception(f"Failed to pause subscription: {str(e)}")

    def resume_subscription(self, subscription_id: str, resume_at: str = "now") -> Dict[str, Any]:
        try:
            response = self.client.subscription.resume(subscription_id, {"resume_at": resume_at})
            return response
        except Exception as e:
            logger.error(f"Razorpay subscription resume failed: {str(e)}")
            raise Exception(f"Failed to resume subscription: {str(e)}")

    def get_subscription_invoices(self, subscription_id: str) -> List[Dict[str, Any]]:
        try:
            # Razorpay doesn't have a direct 'subscription invoices' endpoint in the same way, 
            # but we can fetch invoices filtered by subscription_id
            response = self.client.invoice.all({"subscription_id": subscription_id})
            return response.get("items", [])
        except Exception as e:
            logger.error(f"Razorpay invoice fetch failed: {str(e)}")
            raise Exception(f"Failed to fetch invoices: {str(e)}")

    def get_order_invoices(self, order_id: str) -> List[Dict[str, Any]]:
        try:
            # Fetch invoices filtered by order_id
            response = self.client.invoice.all({"order_id": order_id})
            return response.get("items", [])
        except Exception as e:
            logger.error(f"Razorpay order invoice fetch failed: {str(e)}")
            raise Exception(f"Failed to fetch order invoices: {str(e)}")

    def get_subscription_details(self, subscription_id: str) -> Dict[str, Any]:
        try:
            subscription = self.client.subscription.fetch(subscription_id)
            return subscription
        except Exception as e:
            logger.error(f"Razorpay subscription fetch failed: {str(e)}")
            raise Exception(f"Failed to fetch subscription details: {str(e)}")


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
