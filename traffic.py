import json
import os
from urllib.parse import urlparse

import requests
from flask import session
from langchain_groq import ChatGroq
from markdown import markdown
from config import Config
from backlinks import get_db_connection  # Assuming you have this function to get the DB connection

ChatGroq.model_rebuild()

def _extract_domain(website: str) -> str:
    if not website:
        return ""

    website = website.strip()
    if not website:
        return ""

    parsed = urlparse(website if "://" in website else f"https://{website}")
    domain = (parsed.netloc or parsed.path).strip()

    if domain.startswith("www."):
        domain = domain[4:]

    if "/" in domain:
        domain = domain.split("/", 1)[0]

    return domain

def _to_float(value):
    try:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        return float(str(value).replace("%", "").replace(",", "").strip())
    except Exception:
        return None

def get_traffic_history(website):
    """
    Fetch the traffic history data from the API.
    Insert it into the 'traffic' collection if the user is logged in.
    """
    try:
        domain = _extract_domain(website)
        if not domain:
            return {}, {"error": "Invalid website/domain provided."}
        print(domain)
        api_key = os.getenv("API_KEY3")
        if not api_key:
            return {}, {"error": "Missing RapidAPI key. Set TRAFFIC_RAPIDAPI_KEY (or API_KEY2) environment variable."}

        url = "https://website-traffic-statistics-and-performance-analysis.p.rapidapi.com/data"
        headers = {
            "x-rapidapi-key": api_key,
            "x-rapidapi-host": "website-traffic-statistics-and-performance-analysis.p.rapidapi.com",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

        querystring = {"domain": domain}

        response = requests.get(url, headers=headers, params=querystring, timeout=30)
        print(response)
        content_type = (response.headers.get("Content-Type") or "").lower()
        body_text = response.text or ""

        data = None
        if "application/json" in content_type:
            try:
                data = response.json()
            except Exception:
                data = None
        else:
            stripped = body_text.lstrip()
            if stripped.startswith("{") or stripped.startswith("["):
                try:
                    data = response.json()
                except Exception:
                    data = None

        if response.status_code != 200:
            message = None
            if isinstance(data, dict):
                message = data.get("message") or data.get("error")

            snippet = body_text.strip().replace("\r", " ").replace("\n", " ")[:300]
            if not message:
                message = f"Traffic API request failed with status {response.status_code}."
                if snippet:
                    message = f"{message} Response: {snippet}"

            return {}, {"error": message}

        if data is None:
            snippet = body_text.strip().replace("\r", " ").replace("\n", " ")[:300]
            return {}, {"error": f"Traffic API returned a non-JSON response. Response: {snippet}"}

        if isinstance(data, dict):
            data.setdefault("url", website)
            data.setdefault("domain", domain)

        visitors = []
        if isinstance(data, dict):
            visitors = data.get("visitors_by_country", []) or []

        traffic_history = {}
        for item in visitors:
            if not isinstance(item, dict):
                continue
            country = item.get("country")
            pct = _to_float(item.get("site_traffic_percentage"))
            if country and pct is not None:
                traffic_history[country] = pct

        # Insert into MongoDB if user logged in
        if "email" in session:
            db = get_db_connection()
            traffic_doc = {
                "email": session["email"],
                "url": website,
                "traffic": data  # Save the full API response
            }
            db.traffic.insert_one(traffic_doc)

        return traffic_history, data  # Return both chart data and full data

    except Exception as e:
        print(str(e))
        return {}, {"error": f"Failed to fetch traffic history: {str(e)}"}



def traffic_insights(traffic_data):
    """
    Analyze the traffic data and generate actionable SEO insights,
    then store recommendations in the most recent traffic document for the current user and URL.
    """
    try:
        db = get_db_connection()

        # Initialize the ChatGroq LLM
        llm = ChatGroq(
            temperature=0,
            groq_api_key=os.getenv("LLM_API"),
            model_name="llama-3.3-70b-versatile"
        )

        # Create the prompt
        prompt = (
            "Analyze the following website traffic history data and provide SEO insights "
            "and acquisition strategies for the client to improve their website's performance.\n\n"
            f"Traffic Data:\n{traffic_data}\n"
            "Provide specific and actionable recommendations only for the client in detail."
        )

        # Invoke the LLM
        response = llm.invoke(prompt)
        
        if response:
            recommendations_html = markdown(response.content)

            # If user is logged in, update the most recent traffic document for this URL
            if "email" in session:
    # Find the most recent document for the current user and URL
                latest_document = db.traffic.find_one(
                    {"email": session["email"], "url": traffic_data.get("url")},
                    sort=[("_id", -1)]  # Sort by _id in descending order to get the most recent one
                )

                if latest_document:
                    # Update the most recent document with the recommendations
                    db.traffic.update_one(
                        {"_id": latest_document["_id"]},  # Match by the _id of the latest document
                        {"$set": {"recommendations": recommendations_html}}  # Set the recommendations
                    )

            return recommendations_html
        else:
            return "No recommendations provided."
        
    except Exception as e:
        return f"Failed to generate traffic insights: {str(e)}"

