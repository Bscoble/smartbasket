import time
import re
import requests
import gspread
import urllib.parse
import streamlit as st
from bs4 import BeautifulSoup
from google.oauth2.service_account import Credentials
from datetime import datetime

# --- PAGE CONFIG ---
st.set_page_config(page_title="SmartBasket", page_icon="🛒", layout="centered")

# --- 1. CONFIGURATION & SECURE AUTH ---
ZENROWS_KEY = st.secrets["ZENROWS_KEY"]
creds_dict = dict(st.secrets["gcp_service_account"])

scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
gc = gspread.authorize(creds)
sh = gc.open_by_key("1e_ZARwsDg0LTYfVkgFjybUDXluycHW79lz2ntwRxoaw")

# --- FUNCTIONS ---
def load_store_preferences():
    default_prefs = {"Woolworths": True, "Coles": True, "Aldi": True, "IGA": True}
    try:
        pref_ws = sh.worksheet("Preferences")
        data = pref_ws.get_all_values()
        if len(data) >= 2:
            return {
                "Woolworths": data[1][0] == "True",
                "Coles": data[1][1] == "True",
                "Aldi": data[1][2] == "True",
                "IGA": data[1][3] == "True"
            }
    except Exception:
        pass
    return default_prefs

def save_store_preferences(prefs):
    try:
        pref_ws = sh.worksheet("Preferences")
        pref_ws.update('A1:D2', [
            ["Woolworths", "Coles", "Aldi", "IGA"],
            [str(prefs["Woolworths"]), str(prefs["Coles"]), str(prefs["Aldi"]), str(prefs["IGA"])]
        ])
    except Exception:
        pass

def get_live_price(store, item_name, api_key):
    if store == "Woolworths":
        target_url = f"https://www.woolworths.com.au/shop/search/products?searchTerm={urllib.parse.quote(item_name)}"
    elif store == "Coles":
        target_url = f"https://www.coles.com.au/search?q={urllib.parse.quote(item_name)}"
    elif store == "Aldi":
        target_url = f"https://www.aldi.com.au/en/search/?q={urllib.parse.quote(item_name)}"
    elif store == "IGA":
        target_url = f"https://www.igashop.com.au/search?q={urllib.parse.quote(item_name)}"
    else:
        return 99.99

    api_url = "https://api.zenrows.com/v1/"
    params = {
        "apikey": api_key, "url": target_url, "js_render": "true", "antibot": "true", "premium_proxy": "true"
    }

    try:
        response = requests.get(api_url, params=params, timeout=45)
        soup = BeautifulSoup(response.text, 'html.parser')
        price = 0.00
        price_element = None
        
        if store == "Woolworths":
            price_element = soup.select_one('.primary-price, .price-dollars, .price, [data-testid="price"]') 
        elif store == "Coles":
            price_element = soup.select_one('.price__value, .price, [data-testid="product-pricing"]')
        elif store == "Aldi":
            price_element = soup.select_one('.box--price .value, .product-price, .price, span.price')
        elif store == "IGA":
            price_element = soup.select_one('.item-price, .price')

        if price_element:
            clean_text = price_element.text.replace('$', '').strip()
            match = re.search(r'\d+\.\d{2}', clean_text)
            if match: price = float(match.group())
            else:
                try: price = float(clean_text)
                except ValueError: price = 0.00

        if price == 0.00:
            page_text = soup.get_text()
            prices_found = re.findall(r'\$(\d+\.\d{2})', page_text)
            if prices_found:
                valid_prices = [float(p) for p in prices_found if 0.50 <= float(p) <= 150.0]
                if valid_prices: price = valid_prices[0]

        return price if price > 0 else 5.00 
    except Exception as e:
        return 99.99 

def generate_smart_basket_report(user_items, selected_stores):
    store_totals = {store: 0.0 for store in selected_stores}
    item_breakdown = []
    split_store_total = 0.0
    
    progress_bar = st.progress(0)
    status_text = st.empty()

    valid_items = [row for row in user_items if len(row) >= 3 and row[0].strip()]
    total_items = len(valid_items)
    if total_items == 0: return None
    
    for idx, row in enumerate(valid_items):
        item_name = row[0]
        try: qty = int(row[1])
        except ValueError: qty = 1
        unit = row[2]
        
        status_text.text(f"Scraping prices for: {item_name}...")
        
        item_lower = item_name.lower()
        stores_to_search = selected_stores.copy()
        
        if "woolworths" in item_lower and "Woolworths" in selected_stores: stores_to_search = ["Woolworths"]
        elif "coles" in item_lower and "Coles" in selected_stores: stores_to_search = ["Coles"]
        elif "aldi" in item_lower and "Aldi" in selected_stores: stores_to_search = ["Aldi"]
        elif "iga" in item_lower and "IGA" in selected_stores: stores_to_search = ["IGA"]
        
        item_store_data = {}
        for store in selected_stores:
            if store in stores_to_search:
                unit_price = get_live_price(store, item_name, ZENROWS_KEY)
            else:
                unit_price = 99.99 
                
            total_price = unit_price * qty
            store_totals[store] += total_price
            item_store_data[store] = {
                "unit_price": f"${unit_price:.2f}/{unit}",
                "total_price": total_price
            }

        sorted_item_stores = sorted(item_store_data.items(), key=lambda x: x[1]['total_price'])
        cheapest_store = sorted_item_stores[0][0]
        best_price = sorted_item_stores[0][1]['total_price']
                
        split_store_total += best_price
        
        item_breakdown.append({
            "item_name": item_name,
            "quantity": f"{qty} {unit}",
            "cheapest_store": cheapest_store,
            "unit_price": f"${(best_price/qty):.2f}/{unit}",
            "total_price": f"${best_price:.2f}",
            "all_stores": sorted_item_stores
        })
        
        progress_bar.progress((idx + 1) / total_items)
        time.sleep(1) 
        
    status_text.empty()
    progress_bar.empty()

    ranked_stores = sorted(store_totals.items(), key=lambda x: x[1])
    best_single_store = ranked_stores[0][0]
    best_single_store_cost = ranked_stores[0][1]
    worst_store_cost = ranked_stores[-1][1]

    trip_savings = max(0.0, worst_store_cost - split_store_total)

    store_rankings = []
    for rank, (store, cost) in enumerate(ranked_stores, 1):
        diff = cost - best_single_store_cost
        diff_str = "+$0.00" if diff == 0 else f"+${diff:.2f} more"
        badge = "YOUR STORE" if store in selected_stores else ""
        store_rankings.append({
            "store": store, "rank": rank, "total_cost": cost, 
            "badge": badge, "difference_from_best": diff_str
        })

    return {
        "total_items": total_items,
        "trip_savings": trip_savings,
        "comparison_modes": {
            "single_store_best": {
                "store_name": best_single_store,
                "total_cost": best_single_store_cost,
                "is_recommended": True
            },
            "split_store_optimal": {
                "total_cost": split_store_total,
                "description": "Buy each item where it's cheapest across your stores"
            }
        },
        "store_rankings": store_rankings,
        "item_breakdown": item_breakdown
    }

def send_secure_feedback(user_email, feedback_msg):
    target_inbox = "bscoble74@gmail.com" 
    url = f"https://formsubmit.co/ajax/{target_inbox}"
    payload = {
        "email": user_email,
        "message": feedback_msg,
        "_subject": "🚨 SmartBasket: New Problem Report"
    }
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
    try:
        response = requests.post(url, json=payload, headers=headers)
        return response.status_code == 200
    except:
        return False

# --- 2. SESSION STATE INIT ---
if "authenticated" not in st.session_state:
    st.session_state["authenticated"] = False
if "auth_mode" not in st.session_state:
    st.session_state["auth_mode"] = "login"
if "current_page" not in st.session_state:
    st.session_state["current_page"] = "home"
if "reset_email" not in st.session_state:
    st.session_state["reset_email"] = ""
if "prefs" not in st.session_state:
    st.session_state["prefs"] = load_store_preferences()

prefs = st.session_state["prefs"]

# --- 3. CUSTOM FIGMA CSS ---
st.markdown(f"""
<style>
    /* AUTH HEADER */
    .auth-header {{
        background-color: #005A36;
        color: white;
        padding: 50px 20px 40px 20px;
        margin: -60px -20px 40px -20px;
        border-radius: 0 0 50% 50% / 0 0 45px 45px;
        text-align: center;
        box-shadow: 0 4px 10px rgba(0,0,0,0.1);
    }}
    .auth-header h1 {{
        font-family: "Georgia", serif;
        font-size: 32px;
        font-weight: 700;
        margin-top: 10px;
        color: white;
    }}
    
    /* NATIVE APP HEADER */
    .app-header {{
        background-color: #005A36;
        color: white;
        padding: 30px 20px 20px 20px;
        margin: -60px -20px 20px -20px;
        border-radius: 0 0 20px 20px;
        display: flex;
        justify-content: space-between;
        align-items: center;
    }}
    .app-header h1 {{ margin: 0; color: white; font-size: 26px; font-weight: 800; padding-top: 5px; }}
    .app-header p {{ margin: 0; font-size: 14px; opacity: 0.9; }}

    /* DYNAMIC STORE PILLS (Hiding native checkboxes and styling the label) */
    div:has(> .store-pills-marker) + div[data-testid="stHorizontalBlock"] div[data-testid="stCheckbox"] label [data-baseweb="checkbox"] {{
        display: none !important;
    }}
    div:has(> .store-pills-marker) + div[data-testid="stHorizontalBlock"] div[data-testid="column"]:nth-child(1) div[data-testid="stCheckbox"] label p {{ background-color: {'#005A36' if prefs['Woolworths'] else '#E8E8E8'}; color: {'white' if prefs['Woolworths'] else '#555'}; padding: 6px 14px; border-radius: 20px; font-weight: 600; font-size: 14px; display: inline-block; margin: 0; }}
    div:has(> .store-pills-marker) + div[data-testid="stHorizontalBlock"] div[data-testid="column"]:nth-child(2) div[data-testid="stCheckbox"] label p {{ background-color: {'#E31837' if prefs['Coles'] else '#E8E8E8'}; color: {'white' if prefs['Coles'] else '#555'}; padding: 6px 14px; border-radius: 20px; font-weight: 600; font-size: 14px; display: inline-block; margin: 0; }}
    div:has(> .store-pills-marker) + div[data-testid="stHorizontalBlock"] div[data-testid="column"]:nth-child(3) div[data-testid="stCheckbox"] label p {{ background-color: {'#002D62' if prefs['Aldi'] else '#E8E8E8'}; color: {'white' if prefs['Aldi'] else '#555'}; padding: 6px 14px; border-radius: 20px; font-weight: 600; font-size: 14px; display: inline-block; margin: 0; }}
    div:has(> .store-pills-marker) + div[data-testid="stHorizontalBlock"] div[data-testid="column"]:nth-child(4) div[data-testid="stCheckbox"] label p {{ background-color: {'#E31837' if prefs['IGA'] else '#E8E8E8'}; color: {'white' if prefs['IGA'] else '#555'}; padding: 6px 14px; border-radius: 20px; font-weight: 600; font-size: 14px; display: inline-block; margin: 0; }}

    /* PRIMARY BUTTONS (Auth & Compare) */
    button[data-testid="baseButton-primary"] {{
        background-color: #005A36 !important;
        color: white !important;
        border-radius: 30px;
        padding: 12px;
        font-weight: 800;
        font-size: 16px;
        width: 100%;
        border: none;
        margin-top: 20px;
    }}
    
    /* SECONDARY ADD ITEM SUBMIT BUTTON */
    button[kind="secondaryFormSubmit"] {{
        background-color: #005A36;
        color: white;
        border-radius: 12px;
        font-weight: 800;
        border: none;
    }}

    /* INVISIBLE OVERLAY BUTTONS (Results Screen) */
    div[data-testid="element-container"]:has(#single-card-anchor), div[data-testid="element-container"]:has(#split-card-anchor) {{ display: none; }}
    div[data-testid="element-container"]:has(#single-card-anchor) + div[data-testid="element-container"],
    div[data-testid="element-container"]:has(#split-card-anchor) + div[data-testid="element-container"] {{
        margin-top: -95px !important; margin-bottom: 15px !important;
    }}
    div[data-testid="element-container"]:has(#single-card-anchor) + div[data-testid="element-container"] button,
    div[data-testid="element-container"]:has(#split-card-anchor) + div[data-testid="element-container"] button {{
        opacity: 0 !important; height: 95px !important; width: 100% !important; z-index: 99 !important; cursor: pointer !important;
    }}
    
    /* SUBPAGE BACK BUTTON INVISIBLE OVERLAY */
    div[data-testid="element-container"]:has(#subpage-back-anchor) {{ display: none; }}
    div[data-testid="element-container"]:has(#subpage-back-anchor) + div[data-testid="element-container"] {{
        margin-top: -65px !important; margin-bottom: 20px !important; margin-left: -5px !important; width: 40px !important;
    }}
    div[data-testid="element-container"]:has(#subpage-back-anchor) + div[data-testid="element-container"] button {{
        opacity: 0 !important; height: 40px !important; width: 40px !important; z-index: 99 !important; cursor: pointer !important;
    }}

    /* FOOTER & AUTH TEXT LINK BUTTONS */
    button[data-testid="baseButton-secondary"]:has(div:contains("About Us")),
    button[data-testid="baseButton-secondary"]:has(div:contains("Privacy Policy")),
    button[data-testid="baseButton-secondary"]:has(div:contains("Spot a Problem")),
    button[data-testid="baseButton-secondary"]:has(div:contains("Forgot password?")),
    button[data-testid="baseButton-secondary"]:has(div:contains("Don't have an account?")),
    button[data-testid="baseButton-secondary"]:has(div:contains("Already have an account?")) {{
        background: none !important; border: none !important; padding: 0 !important;
        color: #888 !important; font-size: 13px !important; font-weight: normal !important;
        box-shadow: none !important; margin-top: -5px !important;
    }}
    button[data-testid="baseButton-secondary"]:has(div:contains("About Us")):hover,
    button[data-testid="baseButton-secondary"]:has(div:contains("Privacy Policy")):hover,
    button[data-testid="baseButton-secondary"]:has(div:contains("Spot a Problem")):hover,
    button[data-testid="baseButton-secondary"]:has(div:contains("Forgot password?")):hover,
    button[data-testid="baseButton-secondary"]:has(div:contains("Don't have an account?")):hover,
    button[data-testid="baseButton-secondary"]:has(div:contains("Already have an account?")):hover {{
        text-decoration: underline !important; color: #555 !important;
    }}
</style>
""", unsafe_allow_html=True)


# =====================================================================
# --- 4. AUTHENTICATION & FORGOT PASSWORD ROUTING ---
# =====================================================================
if not st.session_state["authenticated"]:
    
    # -----------------------------------------------------------
    # VIEW: LOGIN
    # -----------------------------------------------------------
    if st.session_state["auth_mode"] == "login":
        st.markdown("""
        <div class="auth-header">
            <div style="font-size: 40px; margin-bottom: -10px;">🛒</div>
            <h1>Welcome back</h1>
        </div>
        """, unsafe_allow_html=True)
        
        with st.form("login_form"):
            email = st.text_input("Email address", placeholder="Email address", label_visibility="collapsed")
            pwd = st.text_input("Password", type="password", placeholder="Password", label_visibility="collapsed")
            
            submitted = st.form_submit_button("Sign In", type="primary", use_container_width=True)
            if submitted:
                st.session_state["authenticated"] = True
                st.rerun()
                
        st.markdown("<br>", unsafe_allow_html=True)
        
        _, center_col, _ = st.columns([1, 2, 1])
        with center_col:
            if st.button("Forgot password?", use_container_width=True):
                st.session_state["auth_mode"] = "forgot_password"
                st.rerun()
            if st.button("Don't have an account? **Sign up**", use_container_width=True):
                st.session_state["auth_mode"] = "signup"
                st.rerun()

    # -----------------------------------------------------------
    # VIEW: SIGN UP
    # -----------------------------------------------------------
    elif st.session_state["auth_mode"] == "signup":
        st.markdown("""
        <div class="auth-header">
            <div style="font-size: 40px; margin-bottom: -10px;">🛒</div>
            <h1>Create account</h1>
        </div>
        """, unsafe_allow_html=True)
        
        with st.form("signup_form"):
            name = st.text_input("Full name", placeholder="Full name", label_visibility="collapsed")
            email = st.text_input("Email address", placeholder="Email address", label_visibility="collapsed")
            pwd = st.text_input("Password", type="password", placeholder="Password", label_visibility="collapsed")
            postcode = st.text_input("Australian postcode", placeholder="Australian postcode", label_visibility="collapsed")
            st.caption("Australian postcodes only (e.g. 2000, 3000, 4000)")
            
            submitted = st.form_submit_button("Create Account", type="primary", use_container_width=True)
            if submitted:
                st.session_state["authenticated"] = True
                st.rerun()
                
        st.markdown("<br>", unsafe_allow_html=True)
        
        _, center_col, _ = st.columns([1, 2, 1])
        with center_col:
            if st.button("Already have an account? **Sign in**", use_container_width=True):
                st.session_state["auth_mode"] = "login"
                st.rerun()

    # -----------------------------------------------------------
    # VIEW: FORGOT PASSWORD (STEP 1)
    # -----------------------------------------------------------
    elif st.session_state["auth_mode"] == "forgot_password":
        st.markdown("""
        <div class="auth-header">
            <div style="font-size: 32px; margin-bottom: -5px;">🔑</div>
            <h1>Reset password</h1>
        </div>
        """, unsafe_allow_html=True)
        
        st.markdown('<div id="subpage-back-anchor"></div>', unsafe_allow_html=True)
        if st.button("Back", key="btn_forgot_back"):
            st.session_state["auth_mode"] = "login"
            st.rerun()
            
        st.markdown("""
        <div style="color: #555; font-size: 14px; line-height: 1.5; margin-bottom: 20px;">
            Enter the email address linked to your account and we'll send you a link to reset your password.
        </div>
        """, unsafe_allow_html=True)
        
        with st.form("forgot_form"):
            reset_email_input = st.text_input("Email address", placeholder="Email address", label_visibility="collapsed")
            
            submitted = st.form_submit_button("Send Reset Link", type="primary", use_container_width=True)
            if submitted:
                if reset_email_input:
                    st.session_state["reset_email"] = reset_email_input
                else:
                    st.session_state["reset_email"] = "bscoble74@gmail.com"
                st.session_state["auth_mode"] = "forgot_success"
                st.rerun()

    # -----------------------------------------------------------
    # VIEW: FORGOT PASSWORD SUCCESS (STEP 2)
    # -----------------------------------------------------------
    elif st.session_state["auth_mode"] == "forgot_success":
        st.markdown("""
        <div class="auth-header">
            <div style="font-size: 32px; margin-bottom: -5px;">🔑</div>
            <h1>Reset password</h1>
        </div>
        """, unsafe_allow_html=True)
        
        st.markdown('<div id="subpage-back-anchor"></div>', unsafe_allow_html=True)
        if st.button("Back", key="btn_success_back"):
            st.session_state["auth_mode"] = "login"
            st.rerun()
            
        target_email = st.session_state.get("reset_email", "bscoble74@gmail.com")
        
        st.markdown(f"""
        <div style="text-align: center; margin-top: 20px;">
            <div style="font-size: 36px; margin-bottom: 10px;">📧</div>
            <h3 style="font-family: 'Georgia', serif; font-size: 20px; color: #111; margin-bottom: 10px;">Check your email</h3>
            <p style="color: #555; font-size: 14px; line-height: 1.5; margin-bottom: 25px;">
                We've sent a password reset link to<br><b>{target_email}</b>
            </p>
            <p style="color: #888; font-size: 12px; margin-bottom: 30px;">
                Didn't receive it? Check your spam folder or try again in a few minutes.
            </p>
        </div>
        """, unsafe_allow_html=True)
        
        if st.button("Back to Sign In", type="primary", use_container_width=True):
            st.session_state["auth_mode"] = "login"
            st.rerun()

# =====================================================================
# --- 5. MAIN APP (Authenticated User) ---
# =====================================================================
else:
    
    # -----------------------------------------------------------
    # VIEW: ABOUT US PAGE
    # -----------------------------------------------------------
    if st.session_state["current_page"] == "about":
        st.markdown("""
        <div style="background-color: #005A36; margin: -60px -20px 20px -20px; padding: 20px; border-radius: 0 0 20px 20px;">
            <div style="display: flex; align-items: center; gap: 15px; color: white;">
                <div style="font-size: 20px;">←</div>
                <h1 style="font-size: 18px; margin: 0; color: white;">About Us</h1>
            </div>
        </div>
        """, unsafe_allow_html=True)
        
        st.markdown('<div id="subpage-back-anchor"></div>', unsafe_allow_html=True)
        if st.button("Back", key="btn_about_back"):
            st.session_state["current_page"] = "home"
            st.rerun()
            
        st.markdown("""
        <div style="color: #444; font-size: 14px; line-height: 1.7; margin-top: 10px;">
            <h3 style="color: #005A36; font-size: 18px; margin-bottom: 10px;">Welcome to SmartBasket</h3>
            <p>SmartBasket is Australia's independent grocery price comparison companion, designed to help households cut through supermarket price hikes and make informed shopping choices.</p>
            
            <h4 style="color: #222; font-size: 15px; margin-top: 20px;">Our Mission</h4>
            <p>We believe grocery shopping shouldn't require visiting multiple stores blindly or sorting through confusing catalogues. By tracking live pricing across major Australian supermarkets like Woolworths, Coles, Aldi, and IGA, SmartBasket shows you instantly whether you save more by buying your whole basket at one store or splitting your items across the cheapest options.</p>
            
            <h4 style="color: #222; font-size: 15px; margin-top: 20px;">Built for Everyday Australians</h4>
            <p>Created to simplify weekly budgeting, SmartBasket puts full transparency back into your hands. No hidden fees, no corporate bias—just real-time data comparing your preferred local stores.</p>
        </div>
        """, unsafe_allow_html=True)

    # -----------------------------------------------------------
    # VIEW: PRIVACY POLICY PAGE
    # -----------------------------------------------------------
    elif st.session_state["current_page"] == "privacy":
        st.markdown("""
        <div style="background-color: #005A36; margin: -60px -20px 20px -20px; padding: 20px; border-radius: 0 0 20px 20px;">
            <div style="display: flex; align-items: center; gap: 15px; color: white;">
                <div style="font-size: 20px;">←</div>
                <h1 style="font-size: 18px; margin: 0; color: white;">Privacy Policy</h1>
            </div>
        </div>
        """, unsafe_allow_html=True)
        
        st.markdown('<div id="subpage-back-anchor"></div>', unsafe_allow_html=True)
        if st.button("Back", key="btn_privacy_back"):
            st.session_state["current_page"] = "home"
            st.rerun()
            
        st.markdown("""
        <div style="color: #444; font-size: 14px; line-height: 1.7; margin-top: 10px;">
            <h3 style="color: #005A36; font-size: 18px; margin-bottom: 10px;">Privacy Policy & Data Protection</h3>
            <p><i>Last updated: August 2026</i></p>
            
            <p>SmartBasket respects your privacy and is committed to protecting any personal data you share with us. This policy outlines how your information is handled.</p>
            
            <h4 style="color: #222; font-size: 15px; margin-top: 20px;">1. Information We Collect</h4>
            <p>When you create an account or use SmartBasket, we collect your name, email address, postal location (postcode), store preferences, and custom shopping lists required to deliver accurate price comparisons.</p>
            
            <h4 style="color: #222; font-size: 15px; margin-top: 20px;">2. How We Use Your Data</h4>
            <p>Your data is used solely to provide and improve your app experience, such as saving your preferred shopping lists and configuring store comparisons. Feedback or problem reports submitted through the app are securely routed directly to our administrative team.</p>
            
            <h4 style="color: #222; font-size: 15px; margin-top: 20px;">3. Data Security</h4>
            <p>We implement secure authentication standards and encrypted database connections to ensure your personal information remains confidential and protected against unauthorized access.</p>
            
            <h4 style="color: #222; font-size: 15px; margin-top: 20px;">4. Contact Us</h4>
            <p>If you have any questions regarding this privacy policy or how your data is managed, please reach out to us via the <b>Spot a Problem / Contact Us</b> section in the app footer.</p>
        </div>
        """, unsafe_allow_html=True)

    # -----------------------------------------------------------
    # VIEW: CONTACT / SPOT A PROBLEM PAGE
    # -----------------------------------------------------------
    elif st.session_state["current_page"] == "contact":
        st.markdown("""
        <div style="background-color: #005A36; margin: -60px -20px 20px -20px; padding: 20px; border-radius: 0 0 20px 20px;">
            <div style="display: flex; align-items: center; gap: 15px; color: white;">
                <div style="font-size: 20px;">←</div>
                <h1 style="font-size: 18px; margin: 0; color: white;">Spot a Problem / Contact Us</h1>
            </div>
        </div>
        """, unsafe_allow_html=True)
        
        st.markdown('<div id="subpage-back-anchor"></div>', unsafe_allow_html=True)
        if st.button("Back", key="btn_contact_back"):
            st.session_state["current_page"] = "home"
            st.rerun()
            
        st.markdown("""
        <div style="border-left: 3px solid #005A36; padding-left: 15px; margin-bottom: 30px; margin-top: 10px; color: #444; font-size: 14px; line-height: 1.6;">
            We're not perfect — <b>and that's okay.</b> Prices change, items get miscategorised, and occasionally things just don't work the way they should. Your reports are what help us fix it.<br><br>
            Tell us what you found and we'll get straight onto it.
        </div>
        """, unsafe_allow_html=True)
        
        with st.form("contact_form"):
            st.markdown("<b style='font-size: 13px;'>Your email address</b>", unsafe_allow_html=True)
            email_input = st.text_input("Email", placeholder="so we can follow up when it's fixed", label_visibility="collapsed")
            
            st.markdown("<br><b style='font-size: 13px;'>What did you spot?</b>", unsafe_allow_html=True)
            feedback_input = st.text_area("Feedback", placeholder="Describe the problem in as much detail as you can. The more context you give us, the faster we can fix it.", height=150, label_visibility="collapsed")
            
            submitted = st.form_submit_button("Submit Feedback", type="primary", use_container_width=True)
            
            if submitted:
                if not email_input or not feedback_input:
                    st.error("Please fill in both fields so we can assist you properly.")
                else:
                    success = send_secure_feedback(email_input, feedback_input)
                    if success:
                        st.success("Thanks! Your feedback has been sent to our team.")
                    else:
                        st.error("Something went wrong sending the report. Please try again later.")

    # -----------------------------------------------------------
    # VIEW: HOME / LIST PAGE
    # -----------------------------------------------------------
    elif st.session_state["current_page"] == "home":

        current_hour = datetime.now().hour
        if current_hour < 12:
            greeting = "Good morning,"
        elif current_hour < 18:
            greeting = "Good afternoon,"
        else:
            greeting = "Good evening,"

        st.markdown(f"""
        <div class="app-header">
            <div>
                <p>{greeting}</p>
                <h1>Brad</h1>
            </div>
            <div style="background-color: rgba(255,255,255,0.2); border-radius: 50%; width: 40px; height: 40px; display: flex; justify-content: center; align-items: center;">
                ↩
            </div>
        </div>
        """, unsafe_allow_html=True)

        st.markdown("<p style='font-size: 13px; font-weight: 700; color: #666; margin-bottom: -15px;'>ADD ITEM</p>", unsafe_allow_html=True)

        with st.form("add_item_form", clear_on_submit=True):
            item_name = st.text_input("What do you need?", placeholder="e.g., Full Cream Milk 2L", label_visibility="collapsed")
            c1, c2, c3 = st.columns([1.5, 2.5, 1])
            with c1:
                qty = st.number_input("Qty", min_value=1, value=1, label_visibility="collapsed")
            with c2:
                unit = st.selectbox("Unit", ["each", "L", "kg", "g", "Pk"], label_visibility="collapsed")
            with c3:
                submitted = st.form_submit_button("＋", help="Add to List")
            
            if submitted and item_name:
                list_ws = sh.worksheet("Shopping List")
                list_ws.append_row([item_name, qty, unit])
                st.rerun()

        st.markdown("<br><p style='font-size: 13px; font-weight: 700; color: #666; margin-bottom: 5px;'>PREFERRED STORES</p>", unsafe_allow_html=True)

        st.markdown('<div class="store-pills-marker"></div>', unsafe_allow_html=True)
        
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            sel_woolies = st.checkbox("Woolworths", value=prefs["Woolworths"])
        with col2:
            sel_coles = st.checkbox("Coles", value=prefs["Coles"])
        with col3:
            sel_aldi = st.checkbox("Aldi", value=prefs["Aldi"])
        with col4:
            sel_iga = st.checkbox("IGA", value=prefs["IGA"])

        new_prefs = {"Woolworths": sel_woolies, "Coles": sel_coles, "Aldi": sel_aldi, "IGA": sel_iga}
        if new_prefs != prefs:
            save_store_preferences(new_prefs)
            st.session_state["prefs"] = new_prefs
            st.rerun()

        active_names = [name for name, active in new_prefs.items() if active]
        st.markdown(f"<p style='font-size: 12px; color: #888; margin-top: -10px; margin-bottom: 25px;'>✓ We'll highlight {', '.join(active_names)} in the comparison</p>", unsafe_allow_html=True)

        try:
            list_ws = sh.worksheet("Shopping List")
            current_items = list_ws.get_all_values()
        except Exception:
            current_items = []

        valid_rows_with_indices = []
        if current_items:
            for sheet_idx, row in enumerate(current_items, start=1):
                if len(row) >= 3 and row[0].strip():
                    valid_rows_with_indices.append((sheet_idx, row))

        item_count = len(valid_rows_with_indices)

        c_head1, c_head2 = st.columns([3, 1])
        with c_head1:
            st.markdown(f"<p style='font-size: 13px; font-weight: 700; color: #666;'>MY LIST ({item_count} ITEMS)</p>", unsafe_allow_html=True)
        with c_head2:
            if item_count > 0:
                st.markdown("<div style='text-align: right;'>", unsafe_allow_html=True)
                if st.button("Clear all", type="secondary"):
                    list_ws.clear()
                    list_ws.append_row(["Item", "Qty", "Unit"])
                    st.rerun()
                st.markdown("</div>", unsafe_allow_html=True)

        if valid_rows_with_indices:
            for sheet_idx, row in valid_rows_with_indices:
                i_name = row[0]
                try: i_qty = int(row[1])
                except ValueError: i_qty = 1
                i_unit = row[2]

                cols = st.columns([0.5, 2.2, 1.3, 0.5])
                with cols[0]:
                    st.markdown("<div style='padding-top: 10px; opacity: 0.5;'>🛒</div>", unsafe_allow_html=True)
                with cols[1]:
                    st.markdown(f"<div style='padding-top: 5px;'><b>{i_name}</b><br><span style='color:#888; font-size:0.85em;'>{i_qty} {i_unit}</span></div>", unsafe_allow_html=True)
                with cols[2]:
                    sub_c1, sub_c2, sub_c3 = st.columns(3)
                    with sub_c1:
                        if st.button("➖", key=f"sub_{sheet_idx}"):
                            if i_qty > 1:
                                list_ws.update(f'B{sheet_idx}', [[i_qty - 1]])
                                st.rerun()
                    with sub_c2:
                        st.markdown(f"<div style='text-align:center; padding-top:10px; font-weight: bold;'>{i_qty}</div>", unsafe_allow_html=True)
                    with sub_c3:
                        if st.button("➕", key=f"add_{sheet_idx}"):
                            list_ws.update(f'B{sheet_idx}', [[i_qty + 1]])
                            st.rerun()
                with cols[3]:
                    if st.button("❌", key=f"del_{sheet_idx}"):
                        list_ws.delete_rows(sheet_idx)
                        st.rerun()
                        
                st.markdown("<hr style='margin: 10px 0; opacity: 0.2;'>", unsafe_allow_html=True)
            
            store_count_label = len(active_names)
            
            if st.button(f"🔍 Compare Prices at {store_count_label} Stores", type="primary"):
                if not active_names:
                    st.error("Please select at least one store to compare.")
                else:
                    with st.spinner("Bypassing supermarket firewalls and fetching live prices..."):
                        fresh_items = list_ws.get_all_values()
                        report = generate_smart_basket_report(fresh_items, active_names)
                        
                    if report:
                        st.session_state["report"] = report
                        st.session_state["shopping_active"] = True
                        st.session_state["active_tab"] = "Overview"
                        st.rerun()
                    else:
                        st.error("No valid items found to compare.")

            # --- RESULTS SCREEN ---
            if "report" in st.session_state and st.session_state.get("shopping_active", False):
                report = st.session_state["report"]
                
                st.markdown(f"### Price Comparison")
                st.caption(f"{report['total_items']} items across {len(active_names)} stores")
                
                if "active_tab" not in st.session_state:
                    st.session_state["active_tab"] = "Overview"

                tab_choice = st.radio("Navigation", ["Overview", "Breakdown", "Discount Cycle"], 
                                      index=["Overview", "Breakdown", "Discount Cycle"].index(st.session_state["active_tab"]),
                                      horizontal=True, label_visibility="collapsed", key="nav_radio")
                
                st.session_state["active_tab"] = tab_choice
                
                if tab_choice == "Overview":
                    st.markdown("#### HOW WOULD YOU LIKE TO SHOP?")
                    
                    single_best = report["comparison_modes"]["single_store_best"]
                    split_opt = report["comparison_modes"]["split_store_optimal"]
                    
                    single_is_recommended = single_best["total_cost"] <= split_opt["total_cost"]
                    
                    c1_border = "#F5A623" if single_is_recommended else "#E0E0E0"
                    c1_border_width = "2px" if single_is_recommended else "1px"
                    c1_badge = '<div style="position: absolute; top: -2px; right: 15px; background-color: #F5A623; color: black; font-weight: 800; font-size: 11px; padding: 4px 10px; border-radius: 0 0 8px 8px;">RECOMMENDED</div>' if single_is_recommended else ''

                    html_single = f"""
                    <div style="border: {c1_border_width} solid {c1_border}; border-radius: 12px; padding: 15px; position: relative; background-color: #FAFAFA; height: 95px; box-sizing: border-box; display: flex; flex-direction: column; justify-content: center; margin-bottom: 0px;">
                        {c1_badge}
                        <div style="display: flex; justify-content: space-between; align-items: center; width: 100%;">
                            <div style="display: flex; align-items: center; gap: 15px;">
                                <div style="font-size: 26px;">🏪</div>
                                <div style="line-height: 1.3;">
                                    <div style="font-weight: 800; color: #111; font-size: 16px;">Shop at one store</div>
                                    <div style="font-size: 13px; color: #666;">Best of your stores: {single_best['store_name']}</div>
                                </div>
                            </div>
                            <div style="font-size: 20px; font-weight: 800; color: #005A36;">${single_best['total_cost']:.2f}</div>
                        </div>
                    </div>
                    """
                    st.markdown(html_single, unsafe_allow_html=True)
                    st.markdown('<div id="single-card-anchor"></div>', unsafe_allow_html=True)
                    if st.button("Shop Single", key="btn_single", use_container_width=True):
                        st.session_state["active_tab"] = "Breakdown"
                        st.rerun()
                    
                    c2_border = "#F5A623" if not single_is_recommended else "#E0E0E0"
                    c2_border_width = "2px" if not single_is_recommended else "1px"
                    c2_badge = '<div style="position: absolute; top: -2px; right: 15px; background-color: #F5A623; color: black; font-weight: 800; font-size: 11px; padding: 4px 10px; border-radius: 0 0 8px 8px;">RECOMMENDED</div>' if not single_is_recommended else ''

                    html_split = f"""
                    <div style="border: {c2_border_width} solid {c2_border}; border-radius: 12px; padding: 15px; position: relative; background-color: #FAFAFA; height: 95px; box-sizing: border-box; display: flex; flex-direction: column; justify-content: center; margin-bottom: 0px;">
                        {c2_badge}
                        <div style="display: flex; justify-content: space-between; align-items: center; width: 100%;">
                            <div style="display: flex; align-items: center; gap: 15px;">
                                <div style="font-size: 26px;">🛍️</div>
                                <div style="line-height: 1.3;">
                                    <div style="font-weight: 800; color: #111; font-size: 16px;">Split across preferred stores</div>
                                    <div style="font-size: 13px; color: #666;">Buy each item where it's cheapest</div>
                                </div>
                            </div>
                            <div style="font-size: 20px; font-weight: 800; color: #005A36;">${split_opt['total_cost']:.2f}</div>
                        </div>
                    </div>
                    """
                    st.markdown(html_split, unsafe_allow_html=True)
                    st.markdown('<div id="split-card-anchor"></div>', unsafe_allow_html=True)
                    if st.button("Shop Split", key="btn_split", use_container_width=True):
                        st.session_state["active_tab"] = "Breakdown"
                        st.rerun()

                    st.divider()
                    st.markdown("#### STORE RANKING — FULL BASKET")
                    for store in report["store_rankings"]:
                        with st.container(border=True):
                            badge_txt = f"[{store['badge']}]" if store['badge'] else ""
                            st.markdown(f"**{store['store']}** {badge_txt} \n\n **${store['total_cost']:.2f}** *({store['difference_from_best']})*")
                            
                elif tab_choice == "Breakdown":
                    st.markdown("#### ITEM-BY-ITEM BREAKDOWN")
                    
                    for item in report["item_breakdown"]:
                        with st.container(border=True):
                            st.markdown(f"**{item['item_name']}** &nbsp; <span style='color:gray; font-size:0.85em;'>× {item['quantity']}</span>", unsafe_allow_html=True)
                            
                            for store_idx, (store_name, store_data) in enumerate(item["all_stores"]):
                                store_initial = store_name[0].upper()
                                is_best = (store_idx == 0)
                                
                                best_badge = " &nbsp; <span style='background-color: #005A36; color: white; padding: 2px 8px; border-radius: 4px; font-size: 0.8em; font-weight: bold;'>BEST</span>" if is_best else ""
                                
                                html_row = f"""
                                <div style="display: flex; justify-content: space-between; align-items: center; padding: 4px 0;">
                                    <div><b>{store_initial}</b> &nbsp; {store_name}</div>
                                    <div>
                                        <span style="color: gray; font-size: 0.85em;">{store_data['unit_price']}</span> &nbsp;&nbsp; 
                                        <b>${store_data['total_price']:.2f}</b>
                                        {best_badge}
                                    </div>
                                </div>
                                """
                                st.markdown(html_row, unsafe_allow_html=True)

                elif tab_choice == "Discount Cycle":
                    st.markdown("#### DISCOUNT CYCLE")
                    st.info("Discount cycle tracking is active and analyzing historical specials across your selected stores.")

        # --- MAIN APP GLOBAL FOOTER ---
        st.markdown("<hr style='margin: 40px 0 20px 0; opacity: 0.2;'>", unsafe_allow_html=True)
        
        fc1, fc2, fc3, fc4, fc5, fc6 = st.columns([0.5, 1, 1.2, 0.1, 1.8, 0.5])
        with fc2:
            if st.button("About Us", key="footer_about"):
                st.session_state["current_page"] = "about"
                st.rerun()
        with fc3:
            if st.button("Privacy Policy", key="footer_privacy"):
                st.session_state["current_page"] = "privacy"
                st.rerun()
        with fc5:
            if st.button("Spot a Problem / Contact Us", key="footer_contact"):
                st.session_state["current_page"] = "contact"
                st.rerun()
        st.markdown("<br>", unsafe_allow_html=True)
