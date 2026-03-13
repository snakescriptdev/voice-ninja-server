from datetime import datetime
from typing import Tuple

def calculate_percentage_change(current: float | int, previous: float | int) -> float:
    """Calculates the percentage change between current and previous values."""
    if previous == 0:
        return 100.0 if current > 0 else 0.0
    return round(((current - previous) / previous) * 100, 2)

def get_current_and_previous_month_start() -> Tuple[datetime, datetime]:
    """Returns the start of the current month and the start of the previous month in UTC."""
    now = datetime.utcnow()
    first_day_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if first_day_of_month.month == 1:
        first_day_prev_month = first_day_of_month.replace(year=first_day_of_month.year - 1, month=12)
    else:
        first_day_prev_month = first_day_of_month.replace(month=first_day_of_month.month - 1)
    return first_day_of_month, first_day_prev_month
