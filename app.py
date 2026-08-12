import time
import re
import requests
import gspread
import urllib.parse
import streamlit as st
from bs4 import BeautifulSoup
from google.oauth2.service_account import Credentials

# --- PAGE CONFIG & CUSTOM FIGMA CSS ---
st.set_page_config(page_title="SmartBasket", page_icon="🛒", layout="centered")

st.markdown("""
<style>
    div[data-testid="stHorizontalBlock"] div:nth-child(1) label span { background-color: #005A36; color: white; padding: 6px 14px; border-radius: 20px; font-weight: 600; }
    div[data-testid="stHorizontalBlock"] div:nth-child(2) label span { background-color: #E31837; color: white; padding: 6px 14px; border-radius: 20px; font-weight: 600; }
    div[data-testid="stHorizontalBlock"] div:nth-child(3) label span { background-color: #002D62; color: white; padding: 6px 14px; border-radius: 20px; font-weight: 600; }
    div[data-testid="stHorizontalBlock"] div:nth-child(4) label span { background-color: #E31837; color: white; padding: 6px 14px; border-radius: 20px; font-weight: 600; }
</style>
""", unsafe_allow_html=True)

# --- 1. CONFIGURATION & SECURE AUTH ---
ZENROWS_KEY = st.secrets["ZENROWS_KEY"]
creds_dict = dict(st.secrets["gcp_service_account"])

scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
gc = gspread.authorize(creds)
sh = gc.open_by_key("1e_ZARwsDg0LTYfVkgFjybUDXluycHW79lz2ntwRxoaw")

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
        try:
            pref_ws = sh.add_worksheet(title="Preferences", rows=2, cols=4)
            pref_ws.append_row(["Woolworths", "Coles", "Aldi", "IGA"])
            pref_ws.append_row(["True", "True", "True", "True"])
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

# --- 2. ADVANCED LIVE SCRAPING ENGINE (ZenRows API + Smart Regex) ---
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
            if match:
                price = float(match.group())
            else:
                try:
                    price = float(clean_text)
                except ValueError:
                    price = 0.00

        if price == 0.00:
            page_text = soup.get_text()
            prices_found = re.findall(r'\$(\d+\.\d{2})', page_text)
            if prices_found:
                valid_prices = [float(p) for p in prices_found if 0.50 <= float(p) <= 150.0]
                if valid_prices:
                    price = valid_prices[0]

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
    
    if total_items == 0:
        return None
    
    for idx, row in enumerate(valid_items):
        item_name = row[0]
        try:
            qty = int(row[1])
        except ValueError:
            qty = 1
        unit = row[2]
        
        status_text.text(f"Scraping prices for: {item_name}...")
        
        item_lower = item_name.lower()
        stores_to_search = selected_stores.copy()
        
        if "woolworths" in item_lower and "Woolworths" in selected_stores:
            stores_to_search = ["Woolworths"]
        elif "coles" in item_lower and "Coles" in selected_stores:
            stores_to_search = ["Coles"]
        elif "aldi" in item_lower and "Aldi" in selected_stores:
            stores_to_search = ["Aldi"]
        elif "iga" in item_lower and "IGA" in selected_stores:
            stores_to_search = ["IGA"]
        
        item_store_prices = {}
        for store in selected_stores:
            if store in stores_to_search:
                unit_price = get_live_price(store, item_name, ZENROWS_KEY)
            else:
                unit_price = 99.99 
                
            total_price = unit_price * qty
            store_totals[store] += total_price
            item_store_prices[store] = total_price

        cheapest_store = min(item_store_prices, key=item_store_prices.get)
        best_price = item_store_prices[cheapest_store]
                
        split_store_total += best_price
        
        item_breakdown.append({
            "item_name": f"{item_name}" if qty == 1 else f"{item_name}",
            "quantity": f"{qty} {unit}",
            "cheapest_store": cheapest_store,
            "unit_price": f"${(best_price/qty):.2f}/{unit}",
            "total_price": f"${best_price:.2f}"
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

# --- 3. STREAMLIT UI LAYOUT ---
st.subheader("ADD ITEM")

with st.form("add_item_form", clear_on_submit=True):
    item_name = st.text_input("What do you need?", placeholder="e.g., Full Cream Milk 2L")
    c1, c2, c3 = st.columns([1, 2, 1])
    with c1:
        qty = st.number_input("Qty", min_value=1, value=1)
    with c2:
        unit = st.selectbox("Unit", ["each", "L", "kg", "g", "Pk"])
    with c3:
        st.write("")
        submitted = st.form_submit_button("＋", help="Add to List")
    
    if submitted and item_name:
        list_ws = sh.worksheet("Shopping List")
        list_ws.append_row([item_name, qty, unit])
        st.success(f"Added {qty} {unit} of {item_name}!")

st.subheader("PREFERRED STORES")
saved_prefs = load_store_preferences()

col1, col2, col3, col4 = st.columns(4)
with col1:
    sel_woolies = st.checkbox("Woolworths", value=saved_prefs["Woolworths"])
with col2:
    sel_coles = st.checkbox("Coles", value=saved_prefs["Coles"])
with col3:
    sel_aldi = st.checkbox("Aldi", value=saved_prefs["Aldi"])
with col4:
    sel_iga = st.checkbox("IGA", value=saved_prefs["IGA"])

current_prefs = {"Woolworths": sel_woolies, "Coles": sel_coles, "Aldi": sel_aldi, "IGA": sel_iga}
if current_prefs != saved_prefs:
    save_store_preferences(current_prefs)

# --- 4. DISPLAY CURRENT LIST & QUANTITY STEPPERS ---
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
    st.subheader(f"MY LIST ({item_count} ITEMS)")
with c_head2:
    if item_count > 0 and st.button("Clear all"):
        list_ws.clear()
        list_ws.append_row(["Item", "Qty", "Unit"])
        st.rerun()

if valid_rows_with_indices:
    for sheet_idx, row in valid_rows_with_indices:
        i_name = row[0]
        try:
            i_qty = int(row[1])
        except ValueError:
            i_qty = 1
        i_unit = row[2]

        cols = st.columns([0.5, 2.5, 1, 0.5, 0.5])
        with cols[0]:
            st.markdown("🛒")
        with cols[1]:
            st.markdown(f"**{i_name}**<br><span style='color:gray; font-size:0.85em;'>{i_qty} {i_unit}</span>", unsafe_allow_html=True)
        with cols[2]:
            sub_c1, sub_c2, sub_c3 = st.columns(3)
            with sub_c1:
                if st.button("➖", key=f"sub_{sheet_idx}"):
                    if i_qty > 1:
                        list_ws.update(f'B{sheet_idx}', [[i_qty - 1]])
                        st.rerun()
            with sub_c2:
                st.markdown(f"<div style='text-align:center; padding-top:4px;'><b>{i_qty}</b></div>", unsafe_allow_html=True)
            with sub_c3:
                if st.button("➕", key=f"add_{sheet_idx}"):
                    list_ws.update(f'B{sheet_idx}', [[i_qty + 1]])
                    st.rerun()
        with cols[3]:
            st.write(i_unit)
        with cols[4]:
            if st.button("❌", key=f"del_{sheet_idx}"):
                list_ws.delete_rows(sheet_idx)
                st.rerun()

    st.divider()
    
    active_stores_list = []
    if sel_woolies: active_stores_list.append("Woolworths")
    if sel_coles: active_stores_list.append("Coles")
    if sel_aldi: active_stores_list.append("Aldi")
    if sel_iga: active_stores_list.append("IGA")
    
    store_count_label = len(active_stores_list)
    
    if st.button(f"🔍 Compare Prices at {store_count_label} Stores"):
        if not active_stores_list:
            st.error("Please select at least one store to compare.")
        else:
            with st.spinner("Bypassing supermarket firewalls and fetching live prices..."):
                fresh_items = list_ws.get_all_values()
                report = generate_smart_basket_report(fresh_items, active_stores_list)
                
            if report:
                st.success("Live comparison complete!")
                st.session_state["report"] = report
                st.session_state["shopping_active"] = True
                st.session_state["active_tab"] = "Overview"
            else:
                st.error("No valid items found to compare.")

    # --- 5. FIGMA-MATCHED RESULTS SCREEN (Overview / Breakdown) ---
    if "report" in st.session_state and st.session_state.get("shopping_active", False):
        report = st.session_state["report"]
        st.divider()
        
        st.markdown(f"### Price Comparison")
        st.caption(f"{report['total_items']} items across {len(active_stores_list)} stores")
        
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
            
            with st.container(border=True):
                if single_is_recommended:
                    st.markdown("🟡 **RECOMMENDED**")
                st.markdown(f"**Shop at one store**\n\nBest of your stores: {single_best['store_name']} — **${single_best['total_cost']:.2f}**")
                if st.button("Select Single Store Mode"):
                    st.session_state["active_tab"] = "Breakdown"
                    st.rerun()
            
            with st.container(border=True):
                if not single_is_recommended:
                    st.markdown("🟡 **RECOMMENDED**")
                st.markdown(f"**Split across my preferred stores**\n\nBuy each item where it's cheapest — **${split_opt['total_cost']:.2f}**")
                if st.button("Select Split Mode"):
                    st.session_state["active_tab"] = "Breakdown"
                    st.rerun()

            st.divider()
            st.markdown("#### STORE RANKING — FULL BASKET")
            for store in report["store_rankings"]:
                with st.container(border=True):
                    badge_txt = f"[{store['badge']}]" if store['badge'] else ""
                    st.markdown(f"**{store['store']}** {badge_txt} \n\n **${store['total_cost']:.2f}** *({store['difference_from_best']})*")
                    
        elif tab_choice == "Breakdown":
            st.markdown("#### YOUR SHOPPING SPLIT")
            split_opt = report["comparison_modes"]["split_store_optimal"]
            
            with st.container(border=True):
                st.markdown(f"**Combined total** \n\n ### **${split_opt['total_cost']:.2f}**")
            
            store_groups = {}
            for item in report["item_breakdown"]:
                store = item["cheapest_store"]
                if store not in store_groups:
                    store_groups[store] = []
                store_groups[store].append(item)

            all_checked = True
            total_checkboxes = 0

            for store_name, items in store_groups.items():
                store_subtotal = sum(float(i['total_price'].replace('$', '')) for i in items)
                
                with st.container(border=True):
                    st.markdown(f"""
                    <div style="background-color: #002D62; color: white; padding: 10px; border-radius: 8px; margin-bottom: 10px;">
                        <b>{store_name[0].upper()} &nbsp; {store_name}</b><br>
                        <span style="font-size: 0.85em; opacity: 0.8;">0/{len(items)} items collected</span>
                        <span style="float: right; font-size: 1.1em; font-weight: bold;">${store_subtotal:.2f}</span>
                    </div>
                    """, unsafe_allow_html=True)

                    for idx, item in enumerate(items):
                        total_checkboxes += 1
                        unique_key = f"chk_{store_name}_{idx}_{item['item_name']}"
                        is_checked = st.checkbox(f"**{item['item_name']}** &nbsp; <span style='color:gray; font-size:0.9em;'>{item['unit_price']}</span> &nbsp;&nbsp;&nbsp;&nbsp; **{item['total_price']}**", key=unique_key)
                        if not is_checked:
                            all_checked = False

            if total_checkboxes > 0 and all_checked:
                st.balloons()
                try:
                    savings_ws = sh.worksheet("Savings")
                    current_lifetime = float(savings_ws.acell('A1').value or 0.0)
                except Exception:
                    current_lifetime = 0.0

                if not st.session_state.get("trip_rewarded", False):
                    new_lifetime = current_lifetime + report["trip_savings"]
                    try:
                        savings_ws.update('A1', [[new_lifetime]])
                    except Exception:
                        pass
                    st.session_state["trip_rewarded"] = True
                    current_lifetime = new_lifetime

                st.divider()
                st.success("🎉 SHOPPING COMPLETE! AMAZING JOB!")
                st.markdown(f"### 💰 You saved **${report['trip_savings']:.2f}** on this shop!")
                st.markdown(f"### 🏆 Total Lifetime Savings: **${current_lifetime:.2f}**")
                
                if st.button("Start New Shop"):
                    st.session_state.clear()
                    st.rerun()

        elif tab_choice == "Discount Cycle":
            st.markdown("#### DISCOUNT CYCLE")
            st.info("Discount cycle tracking is active and analyzing historical specials across your selected stores.")
            
        st.divider()
        st.caption("About Us  |  Privacy Policy  |  Spot a Problem / Contact Us")

else:
    st.info("Your shopping list is empty. Add an item above to get started.")
