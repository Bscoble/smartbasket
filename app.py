import time
import re
import requests
import gspread
import urllib.parse
import streamlit as st
from bs4 import BeautifulSoup
from google.oauth2.service_account import Credentials

# --- 1. CONFIGURATION & SECURE AUTH ---
ZENROWS_KEY = st.secrets["ZENROWS_KEY"]
creds_dict = dict(st.secrets["gcp_service_account"])

scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
gc = gspread.authorize(creds)
sh = gc.open_by_key("1e_ZARwsDg0LTYfVkgFjybUDXluycHW79lz2ntwRxoaw")

# --- 2. ADVANCED LIVE SCRAPING ENGINE (ZenRows API + Smart Regex) ---
def get_live_price(store, item_name, api_key):
    """Fetches the live price using ZenRows API and resilient regex price extraction."""
    
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
        "apikey": api_key,
        "url": target_url,
        "js_render": "true",      
        "antibot": "true",        
        "premium_proxy": "true"   
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
        
        best_price = float('inf')
        cheapest_store = None
        
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
        
        for store in selected_stores:
            if store in stores_to_search:
                unit_price = get_live_price(store, item_name, ZENROWS_KEY)
            else:
                unit_price = 99.99 
                
            total_price = unit_price * qty
            store_totals[store] += total_price
            
            if store in stores_to_search and total_price < best_price:
                best_price = total_price
                cheapest_store = store
                
        split_store_total += best_price
        
        item_breakdown.append({
            "item_name": item_name,
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
    worst_store_cost = ranked_stores[-1][1] # Used to calculate maximum savings vs worst store/single store
    best_single_store = ranked_stores[0][0]
    best_single_store_cost = ranked_stores[0][1]

    # Calculate total money saved on this trip (Difference between worst single store trip and optimal split trip)
    trip_savings = max(0.0, worst_store_cost - split_store_total)

    store_rankings = []
    for rank, (store, cost) in enumerate(ranked_stores, 1):
        diff = cost - best_single_store_cost
        diff_str = "+$0.00" if diff == 0 else f"+${diff:.2f} more"
        badge = "YOUR STORE" if diff == 0 else ""
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
st.title("🛒 SmartBasket")
st.write("Shop smarter. Save every week.")

st.subheader("Preferred Stores")
col1, col2, col3, col4 = st.columns(4)
with col1:
    sel_woolies = st.checkbox("Woolworths", value=True)
with col2:
    sel_coles = st.checkbox("Coles", value=True)
with col3:
    sel_aldi = st.checkbox("Aldi", value=True)
with col4:
    sel_iga = st.checkbox("IGA", value=True)

st.subheader("Add Item")
with st.form("add_item_form", clear_on_submit=True):
    item_name = st.text_input("What do you need?", placeholder="e.g., Full Cream Milk 2L")
    qty = st.number_input("Quantity", min_value=1, value=1)
    unit = st.selectbox("Unit", ["each", "L", "kg", "g", "Pk"])
    submitted = st.form_submit_button("Add to List")
    
    if submitted and item_name:
        list_ws = sh.worksheet("Shopping List")
        list_ws.append_row([item_name, qty, unit])
        st.success(f"Added {qty} {unit} of {item_name} to your list!")

# --- 4. DISPLAY CURRENT LIST & DELETE FUNCTIONALITY ---
st.subheader("My List")
try:
    list_ws = sh.worksheet("Shopping List")
    current_items = list_ws.get_all_values()
except Exception:
    current_items = []

if current_items:
    valid_rows_with_indices = []
    for sheet_idx, row in enumerate(current_items, start=1):
        if len(row) >= 3 and row[0].strip():
            valid_rows_with_indices.append((sheet_idx, row))

    if valid_rows_with_indices:
        for sheet_idx, row in valid_rows_with_indices:
            col_item, col_del = st.columns([4, 1])
            with col_item:
                st.write(f"• **{row[0]}** ({row[1]} {row[2]})")
            with col_del:
                if st.button("🗑️", key=f"del_{sheet_idx}"):
                    list_ws.delete_rows(sheet_idx)
                    st.success(f"Removed {row[0]}")
                    st.rerun()

        st.divider()
        
        if st.button("Compare Prices Across Stores"):
            active_stores = []
            if sel_woolies: active_stores.append("Woolworths")
            if sel_coles: active_stores.append("Coles")
            if sel_aldi: active_stores.append("aldi")
            if sel_iga: active_stores.append("IGA")
            
            if not active_stores:
                st.error("Please select at least one store to compare.")
            else:
                with st.spinner("Bypassing supermarket firewalls and fetching live prices... This may take a minute."):
                    fresh_items = list_ws.get_all_values()
                    report = generate_smart_basket_report(fresh_items, active_stores)
                    
                if report:
                    st.success("Live comparison complete!")
                    st.session_state["report"] = report
                    st.session_state["shopping_active"] = True
                else:
                    st.error("No valid items found to compare.")

        # --- 5. INTERACTIVE STORE CHECKLISTS & CELEBRATION SCREEN ---
        if "report" in st.session_state and st.session_state.get("shopping_active", False):
            report = st.session_state["report"]
            st.divider()
            st.subheader("🛒 Optimal Split-Shop Checklists")
            st.caption("Tick off items as you walk through each store.")

            store_groups = {}
            for item in report["item_breakdown"]:
                store = item["cheapest_store"]
                if store not in store_groups:
                    store_groups[store] = []
                store_groups[store].append(item)

            all_checked = True
            total_checkboxes = 0

            for store_name, items in store_groups.items():
                with st.expander(f"📍 {store_name} ({len(items)} items)", expanded=True):
                    for idx, item in enumerate(items):
                        total_checkboxes += 1
                        unique_key = f"chk_{store_name}_{idx}_{item['item_name']}"
                        is_checked = st.checkbox(f"{item['item_name']} ({item['quantity']}) — {item['total_price']}", key=unique_key)
                        if not is_checked:
                            all_checked = False

            # If every single checkbox across all stores is ticked, trigger the celebration screen!
            if total_checkboxes > 0 and all_checked:
                st.balloons()
                
                # Update lifetime savings in Google Sheets
                try:
                    savings_ws = sh.worksheet("Savings")
                    current_lifetime = float(savings_ws.acell('A1').value or 0.0)
                except Exception:
                    current_lifetime = 0.0

                # Prevent double-counting the same trip session
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

    else:
        st.info("Your shopping list is empty. Add an item above to get started.")
else:
    st.info("Your shopping list is empty. Add an item above to get started.")
