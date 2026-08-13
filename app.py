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

# --- 2. SESSION STATE INIT ---
if "authenticated" not in st.session_state:
    st.session_state["authenticated"] = False
if "auth_mode" not in st.session_state:
    st.session_state["auth_mode"] = "login"
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
</style>
""", unsafe_allow_html=True)


# =====================================================================
# --- 4. AUTHENTICATION ROUTING ---
# =====================================================================
if not st.session_state["authenticated"]:
    
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
        
        # Center the toggle buttons
        _, center_col, _ = st.columns([1, 2, 1])
        with center_col:
            st.button("Forgot password?", use_container_width=True)
            if st.button("Don't have an account? **Sign up**", use_container_width=True):
                st.session_state["auth_mode"] = "signup"
                st.rerun()

    else:
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

else:
    # =====================================================================
    # --- 5. MAIN APP (Authenticated User) ---
    # =====================================================================

    # Determine dynamic greeting based on current time
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

    # Invisible marker to isolate pill CSS logic
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
    st.markdown("""
    <hr style='margin: 40px 0 20px 0; opacity: 0.2;'>
    <div style="text-align: center; font-size: 12px;">
        <a href="#" style="color: #888; text-decoration: none;">About Us</a> &nbsp;|&nbsp; 
        <a href="#" style="color: #888; text-decoration: none;">Privacy Policy</a> &nbsp;|&nbsp; 
        <a href="#" style="color: #888; text-decoration: none;">Spot a Problem / Contact Us</a>
    </div>
    <br>
    """, unsafe_allow_html=True)
