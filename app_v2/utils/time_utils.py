from datetime import datetime


def format_time_ago(dt: datetime) -> str:
    now = datetime.utcnow()
    diff = now - dt

    if diff.days > 365:
        years = diff.days // 365
        return f"{years} year{'s' if years > 1 else ''} ago"
    if diff.days > 30:
        months = diff.days // 30
        return f"{months} month{'s' if months > 1 else ''} ago"
    if diff.days > 0:
        return f"{diff.days} day{'s' if diff.days > 1 else ''} ago"
    
    seconds = diff.seconds
    if seconds > 3600:
        hours = seconds // 3600
        return f"{hours} hour{'s' if hours > 1 else ''} ago"
    if seconds > 60:
        minutes = seconds // 60
        return f"{minutes} minute{'s' if minutes > 1 else ''} ago"
    
    return "just now"
