"""
Feedback and contact module for Grocery Gecko.
Handles secure feedback submission and contact form functionality.
"""

import logging
from typing import Any, Optional
import requests
from config import ADMIN_EMAIL, FEEDBACK_URL_TEMPLATE, FEEDBACK_SUBJECT, REQUEST_TIMEOUT
from helpers import validate_email

logger = logging.getLogger(__name__)

MAX_SCREENSHOT_SIZE_BYTES = 5 * 1024 * 1024  # 5MB, matches formsubmit.co's own attachment limit
ALLOWED_SCREENSHOT_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}


class FeedbackManager:
    """Handles user feedback and contact submissions."""
    
    @staticmethod
    def send_feedback(user_email: str, feedback_msg: str, screenshot: Optional[Any] = None) -> bool:
        """
        Send user feedback securely to admin email via formsubmit.co.
        
        Args:
            user_email: User's email address
            feedback_msg: Feedback message
            screenshot: Optional uploaded file (e.g. Streamlit's UploadedFile) with
                .name, .type, and .getvalue(). Silently dropped if too large or an
                unsupported type, so a bad attachment never blocks the report itself.
            
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

        if screenshot is not None:
            screenshot_type = getattr(screenshot, "type", "")
            if screenshot_type not in ALLOWED_SCREENSHOT_TYPES:
                logger.warning(f"Unsupported screenshot type '{screenshot_type}'; sending feedback without it")
                screenshot = None
            elif len(screenshot.getvalue()) > MAX_SCREENSHOT_SIZE_BYTES:
                logger.warning("Screenshot attachment too large; sending feedback without it")
                screenshot = None
        
        try:
            url = FEEDBACK_URL_TEMPLATE.format(ADMIN_EMAIL)
            fields = {
                "email": user_email,
                "message": feedback_msg,
                "_subject": FEEDBACK_SUBJECT,
            }
            
            logger.debug(f"Submitting feedback from {user_email}")
            if screenshot is not None:
                # formsubmit.co only accepts attachments via multipart form-data,
                # not the JSON body used for plain text feedback.
                files = {"attachment": (screenshot.name, screenshot.getvalue(), screenshot.type)}
                response = requests.post(url, data=fields, files=files, timeout=REQUEST_TIMEOUT)
            else:
                headers = {
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                }
                response = requests.post(url, json=fields, headers=headers, timeout=REQUEST_TIMEOUT)
            
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
