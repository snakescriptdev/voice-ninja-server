import os
import json
import requests


URL = "https://api.cal.com/v2/bookings"


class CalService:
    def __init__(self):
        self.url = URL
        self.headers = {
            "Authorization": os.getenv("CAL_API_KEY"),
            "cal-api-version": "2024-08-13",
        }

    def get_bookings(self):
        response = requests.get(self.url, headers=self.headers)
        json_response = json.dumps(response.json(), indent=4)
        print(json_response)



cal_service = CalService()
cal_service.get_bookings()
