"""
Footer component for SmartBasket application.
Provides a reusable, attractive footer with navigation links and copyright.
"""

import streamlit as st


def render_footer(on_about, on_privacy, on_support, on_refer):
    """
    Render an enhanced footer with navigation links.
    
    Args:
        on_about: Callback function for About button
        on_privacy: Callback function for Privacy button
        on_support: Callback function for Support button
        on_refer: Callback function for Refer button
    """
    st.markdown("<hr style='margin: 30px 0 20px 0; opacity: 0.1;'>", unsafe_allow_html=True)
    
    # Enhanced footer with grid layout, icons, hover effects
    footer_html = """
    <div style="padding: 20px 0 10px 0; text-align: center;">
        <div style="display: grid; grid-template-columns: 1fr 1fr 1fr 1fr; gap: 8px; margin-bottom: 15px;">
            <div style="display: flex; flex-direction: column; align-items: center; gap: 6px; padding: 12px 8px; border-radius: 10px; text-decoration: none; transition: all 0.2s ease; background-color: #FAFAFA; border: 1px solid #E8E8E8; cursor: pointer;" onmouseover="this.style.backgroundColor='#F5F5F5'; this.style.borderColor='#005A36';" onmouseout="this.style.backgroundColor='#FAFAFA'; this.style.borderColor='#E8E8E8';">
                <span style="font-size: 16px;">ℹ️</span>
                <span style="font-size: 11px; font-weight: 600; color: #333;">About</span>
            </div>
            <div style="display: flex; flex-direction: column; align-items: center; gap: 6px; padding: 12px 8px; border-radius: 10px; text-decoration: none; transition: all 0.2s ease; background-color: #FAFAFA; border: 1px solid #E8E8E8; cursor: pointer;" onmouseover="this.style.backgroundColor='#F5F5F5'; this.style.borderColor='#005A36';" onmouseout="this.style.backgroundColor='#FAFAFA'; this.style.borderColor='#E8E8E8';">
                <span style="font-size: 16px;">🔒</span>
                <span style="font-size: 11px; font-weight: 600; color: #333;">Privacy</span>
            </div>
            <div style="display: flex; flex-direction: column; align-items: center; gap: 6px; padding: 12px 8px; border-radius: 10px; text-decoration: none; transition: all 0.2s ease; background-color: #FAFAFA; border: 1px solid #E8E8E8; cursor: pointer;" onmouseover="this.style.backgroundColor='#F5F5F5'; this.style.borderColor='#005A36';" onmouseout="this.style.backgroundColor='#FAFAFA'; this.style.borderColor='#E8E8E8';">
                <span style="font-size: 16px;">💬</span>
                <span style="font-size: 11px; font-weight: 600; color: #333;">Support</span>
            </div>
            <div style="display: flex; flex-direction: column; align-items: center; gap: 6px; padding: 12px 8px; border-radius: 10px; text-decoration: none; transition: all 0.2s ease; background-color: #FAFAFA; border: 1px solid #E8E8E8; cursor: pointer;" onmouseover="this.style.backgroundColor='#F5F5F5'; this.style.borderColor='#005A36';" onmouseout="this.style.backgroundColor='#FAFAFA'; this.style.borderColor='#E8E8E8';">
                <span style="font-size: 16px;">👥</span>
                <span style="font-size: 11px; font-weight: 600; color: #333;">Refer</span>
            </div>
        </div>
        <p style="margin: 10px 0 0 0; font-size: 10px; color: #999; font-weight: 500;">© 2026 SmartBasket • Shop Smarter, Save Every Week</p>
    </div>
    """
    st.markdown(footer_html, unsafe_allow_html=True)
    
    # Hidden button row for Streamlit functionality
    st.markdown('<div class="footer-buttons-marker"></div>', unsafe_allow_html=True)
    fc1, fc2, fc3, fc4 = st.columns([1, 1.2, 1.2, 1.4])
    
    with fc1:
        if st.button("About", key="footer_about"):
            on_about()
    
    with fc2:
        if st.button("Privacy", key="footer_privacy"):
            on_privacy()
    
    with fc3:
        if st.button("Support", key="footer_contact"):
            on_support()
    
    with fc4:
        if st.button("Refer a Friend", key="footer_refer"):
            on_refer()
