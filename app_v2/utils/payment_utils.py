import razorpay
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from app_v2.core.config import VoiceSettings
from app_v2.schemas.enum_types import BillingPeriodEnum
import requests
from app_v2.core.logger import setup_logger
from requests.auth import HTTPBasicAuth
from typing import List, Dict, Any
import hmac
import hashlib
logger = setup_logger(__name__)

# RAZORPAY_WEBHOOK_SECRET = "74e376355beeaa08d3297918c1eac2679fb40307515e545f6e77fbba48f17a78"
# WEBHOOK_SUBSCRIPTION_EVENTS= [
#     "subscription.authenticated",
#     "subscription.activated",
#     "subscription.charged",
#     "subscription.completed",
#     "subscription.updated",
#     "subscription.pending",
#     "subscription.halted",
#     "subscription.cancelled",
#     "subscription.paused",
#     "subscription.resumed"
# ]
# ALERT_EMAIL = "vikram@snakescript.com"

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
    def update_subscription(self, subscription_id: str, plan_id: str, offer_id: Optional[str] = None, schedule_change_at: str = "cycle_end") -> Dict[str, Any]:
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

    def update_subscription(self, subscription_id: str, new_plan_id: str, offer_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Update subscription by:
        1. Cancelling the existing subscription at cycle end
        2. Fetching customer_id from cancel response
        3. Creating a new subscription with the new plan
        """

        try:
            logger.info(f"Cancelling subscription {subscription_id} at cycle end")

            # Step 1: Cancel existing subscription at cycle end
            cancel_response = self.client.subscription.cancel(
                subscription_id,
                {"cancel_at_cycle_end": True}
            )

            logger.info(f"Cancel response: {cancel_response}")

            # Step 2: Extract customer_id
            customer_id = cancel_response.get("customer_id")

            if not customer_id:
                raise Exception("customer_id not found in cancel response")

            logger.info(f"Customer ID retrieved: {customer_id}")

            # Step 3: Create new subscription
            payload = {
                "plan_id": new_plan_id,
                "customer_id": customer_id,
                "total_count": 12
            }

            if offer_id:
                payload["offer_id"] = offer_id

            logger.info(f"Creating new subscription with plan {new_plan_id}")

            new_subscription = self.client.subscription.create(payload)

            logger.info(f"New subscription created: {new_subscription['id']}")

            return {
                "cancelled_subscription": cancel_response,
                "new_subscription": new_subscription
            }

        except Exception as e:
            logger.error(f"Failed to update subscription: {str(e)}")
            raise Exception(f"Subscription update failed: {str(e)}")
    
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

    def create_subscription_webhook(
        self,
        account_id: str,
        url: str,
        secret: str,
        alert_email: str,
        events: List[str],
    ) -> Dict[str, Any]:

            endpoint = f"{self.base_url}/v2/accounts/{account_id}/webhooks"

            payload = {
                "url": url,
                "alert_email": alert_email,
                "secret": secret,
                "events": events,
            }

            logger.info(
                "Creating Razorpay webhook | account_id=%s | url=%s | events=%s",
                account_id,
                url,
                events,
            )

            try:
                response = requests.post(
                    endpoint,
                    auth=HTTPBasicAuth(
                        VoiceSettings.RAZOR_KEY_ID,
                        VoiceSettings.RAZOR_KEY_SECRET,
                    ),
                    json=payload,
                    timeout=15,
                )

                logger.debug(
                    "Razorpay webhook response | status_code=%s | body=%s",
                    response.status_code,
                    response.text,
                )

                if response.status_code not in [200, 201]:
                    logger.error(
                        "Webhook creation failed | status=%s | response=%s",
                        response.status_code,
                        response.text,
                    )
                    raise Exception(
                        f"Webhook creation failed: {response.status_code} - {response.text}"
                    )

                webhook_data = response.json()

                logger.info(
                    "Webhook created successfully | webhook_id=%s",
                    webhook_data.get("id"),
                )

                return {
                    "webhook_id": webhook_data.get("id"),
                    "provider_metadata": webhook_data,
                }

            except requests.exceptions.Timeout:
                logger.exception("Razorpay webhook creation timeout")
                raise Exception("Razorpay webhook request timed out")

            except requests.exceptions.RequestException as e:
                logger.exception("Razorpay webhook request error")
                raise Exception(f"Razorpay webhook request failed: {str(e)}")

            except Exception as e:
                logger.exception("Unexpected error during webhook creation")
                raise


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
