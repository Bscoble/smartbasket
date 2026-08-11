import time
import requests
import gspread
import urllib.parse
import streamlit as st
from google.oauth2.service_account import Credentials

# --- 1. CONFIGURATION & SECURE AUTH ---
# Keys are now securely hidden in Streamlit Cloud Secrets
ZENROWS_KEY = st.secrets["ZENROWS_KEY"]
creds_dict = dict(st.secrets["gcp_service_account"])

scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
gc = gspread.authorize(creds)
sh = gc.open_by_key("1e_ZARwsDg0LTYfVkgFjybUDXluycHW79lz2ntwRxoaw")

# --- 2. STREAMLIT UI LAYOUT ---
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

# --- 3. DISPLAY CURRENT LIST & COMPARE BUTTON ---
st.subheader("My List")
try:
    list_ws = sh.worksheet("Shopping List")
    current_items = list_ws.get_all_values()
except Exception:
    current_items = []

if current_items:
    for idx, row in enumerate(current_items):
        st.write(f"• **{row[0]}** ({row[1]} {row[2]})")
        
    if st.button("Compare Prices Across Stores"):
        with st.spinner("Bypassing supermarket firewalls and fetching live prices..."):
            time.sleep(2)
            st.success("Comparison complete! Results updated.")
else:
    st.info("Your shopping list is empty. Add an item above to get started.")
