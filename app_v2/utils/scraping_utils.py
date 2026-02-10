from bs4 import BeautifulSoup
import requests


def scrape_webpage_title(url:str)->str:
    try:
        response = requests.get(url,timeout=10)

        response.raise_for_status()

    except requests.exceptions.RequestException as e:
        print(f"Error fetching the URL: {e}")
        exit()
    
    soup = BeautifulSoup(response.content,"html.parser")
    title = soup.title.string if soup.title else "No title found"
    return title



