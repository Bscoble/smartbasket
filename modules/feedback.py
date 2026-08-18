"""
Feedback and contact module for SmartBasket.
Handles secure feedback submission and contact form functionality.
"""

import logging
from typing import Optional
import requests
from config import ADMIN_EMAIL, FEEDBACK_URL_TEMPLATE, FEEDBACK_SUBJECT, REQUEST_TIMEOUT
from helpers import validate_email

logger = logging.getLogger(__name__)


class FeedbackManager:
    """Handles user feedback and contact submissions."""
    
    @staticmethod
    def send_feedback(user_email: str, feedback_msg: str) -> bool:
        """
        Send user feedback securely to admin email via formsubmit.co.
        
        Args:
            user_email: User's email address
            feedback_msg: Feedback message
            
        Returns:
            True if submission successful, False otherwise
        """
        # Validate inputs
        if not user_email or not feedback_msg:
            logger.warning("Feedback submission missing required fields")
            return False
        
        if not validate_email(user_email):
            logger.warning(f"Invalid email address: {user_email}")
            return False
        
        if len(feedback_msg.strip()) < 10:
            logger.warning("Feedback message too short")
            return False
        
        try:
            url = FEEDBACK_URL_TEMPLATE.format(ADMIN_EMAIL)
            payload = {
                "email": user_email,
                "message": feedback_msg,
                "_subject": FEEDBACK_SUBJECT,
            }
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
            
            logger.debug(f"Submitting feedback from {user_email}")
            response = requests.post(
                url,
                json=payload,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            )
            
            if response.status_code == 200:
                logger.info(f"Feedback submitted successfully from {user_email}")
                return True
            else:
                logger.error(
                    f"Feedback submission failed with status {response.status_code}: {response.text}"
                )
                return False
        except requests.RequestException as e:
            logger.error(f"Request error submitting feedback: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error submitting feedback: {e}", exc_info=True)
            return False
