import razorpay
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from app_v2.core.config import VoiceSettings
import requests
from app_v2.core.logger import setup_logger
from requests.auth import HTTPBasicAuth
from typing import List, Dict, Any
import hmac
import hashlib
logger = setup_logger(__name__)
import datetime



class BasePaymentProvider(ABC):
    @abstractmethod
    def create_order(self, amount: float, currency: str, receipt: str, notes: Optional[Dict] = None) -> Dict[str, Any]:
        pass

    @abstractmethod
    def verify_order_signature(self, params: Dict[str, str]) -> bool:
        pass

    @abstractmethod
    def get_order_invoices(self, order_id: str) -> List[Dict[str, Any]]:
        pass

class RazorpayProvider(BasePaymentProvider):
    def __init__(self):
        self.client = razorpay.Client(auth=(VoiceSettings.RAZOR_KEY_ID, VoiceSettings.RAZOR_KEY_SECRET))

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

    def get_order_invoices(self, order_id: str) -> List[Dict[str, Any]]:
        try:
            # Fetch invoices filtered by order_id
            response = self.client.invoice.all({"order_id": order_id})
            return response.get("items", [])
        except Exception as e:
            logger.error(f"Razorpay order invoice fetch failed: {str(e)}")
            raise Exception(f"Failed to fetch order invoices: {str(e)}")


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
