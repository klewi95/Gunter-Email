import streamlit as st
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import anthropic
import base64
from email.mime.text import MIMEText
from datetime import datetime
import time

# ====================
# Utility Functions
# ====================

def initialize_session_state():
    """Initializes the session state variables."""
    if 'is_monitoring' not in st.session_state:
        st.session_state.is_monitoring = False
    if 'last_check' not in st.session_state:
        st.session_state.last_check = None
    if 'current_email' not in st.session_state:
        st.session_state.current_email = None
    if 'current_response' not in st.session_state:
        st.session_state.current_response = None
    if 'email_history' not in st.session_state:
        st.session_state.email_history = []

def apply_custom_css():
    """Applies custom CSS."""
    st.markdown("""
        <style>
        .email-container {
            background-color: #f8f9fa;
            border-radius: 5px;
            padding: 15px;
            margin: 10px 0;
        }
        .email-header {
            border-bottom: 1px solid #dee2e6;
            padding-bottom: 10px;
            margin-bottom: 10px;
        }
        .response-container {
            background-color: #e9ecef;
            border-radius: 5px;
            padding: 15px;
            margin: 10px 0;
        }
        .stButton button {
            width: 100%;
        }
        </style>
    """, unsafe_allow_html=True)

def parse_base64_content(encoded_data: str) -> str:
    """Decodes base64-encoded strings safely."""
    try:
        return base64.urlsafe_b64decode(encoded_data).decode('utf-8', errors='replace')
    except Exception:
        return ""

def sanitize_subject(subject: str) -> str:
    """Ensure we only add 'Re:' once to the subject."""
    if subject.lower().startswith("re:"):
        return subject
    return f"Re: {subject}"

# ====================
# Main EmailBot Class
# ====================

class EmailBot:
    def __init__(self):
        self.SCOPES = ['https://www.googleapis.com/auth/gmail.modify']
        self.sender_email = st.secrets["gmail_sender"]
        self.target_email = st.secrets["gmail_target"]
        
        # Create Gmail API service
        self.service = self.setup_gmail()

        # Create the Anthropic client (no `proxies` argument!)
        self.claude = anthropic.Client(
            api_key=st.secrets["claude_api_key"]
        )

    def setup_gmail(self):
        """Creates a Gmail API service instance using stored credentials."""
        if 'gmail_token' not in st.session_state:
            st.session_state.gmail_token = st.secrets.get("gmail_token", {})

        creds = None
        if st.session_state.gmail_token:
            creds = Credentials.from_authorized_user_info(
                st.session_state.gmail_token, 
                self.SCOPES
            )

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                st.error("Gmail-Authentifizierung erforderlich. Bitte fügen Sie den Token zu den Secrets hinzu.")
                st.stop()

        return build('gmail', 'v1', credentials=creds)

    def get_unread_emails(self):
        """Retrieves unread messages from a specific sender."""
        query = f'from:{self.target_email} is:unread'
        try:
            results = self.service.users().messages().list(
                userId='me', q=query
            ).execute()
            return results.get('messages', [])
        except Exception as e:
            st.error(f"Fehler beim Abrufen der E-Mails: {e}")
            return []

    def get_email_content(self, msg_id: str) -> str:
        """
        Fetches the full email content. 
        Tries 'text/plain' first, then falls back to 'text/html'.
        """
        try:
            message = self.service.users().messages().get(
                userId='me', 
                id=msg_id, 
                format='full'
            ).execute()
            if 'payload' in message:
                parts = message['payload'].get('parts', [])
                
                # If the email has multiple parts
                if parts:
                    # 1) Try text/plain part
                    for part in parts:
                        if part.get('mimeType') == 'text/plain':
                            return parse_base64_content(part['body'].get('data', ''))

                    # 2) If no text/plain, fall back to text/html
                    for part in parts:
                        if part.get('mimeType') == 'text/html':
                            return parse_base64_content(part['body'].get('data', ''))

                # Single-part emails
                elif 'body' in message['payload']:
                    return parse_base64_content(message['payload']['body'].get('data', ''))
            return ""
        except Exception as e:
            st.error(f"Fehler beim Lesen der E-Mail: {e}")
            return ""

    def get_email_details(self, msg_id: str):
        """
        Retrieves essential email headers and the content.
        Returns a dict with 'id', 'subject', 'date', 'from', 'content'.
        """
        try:
            message = self.service.users().messages().get(
                userId='me', 
                id=msg_id, 
                format='full'
            ).execute()

            headers = message['payload'].get('headers', [])
            subject = next((h['value'] for h in headers if h['name'].lower() == 'subject'), 'Kein Betreff')
            date = next((h['value'] for h in headers if h['name'].lower() == 'date'), 'Kein Datum')
            from_email = next((h['value'] for h in headers if h['name'].lower() == 'from'), 'Unbekannter Absender')

            content = self.get_email_content(msg_id)
            return {
                'id': msg_id,
                'subject': subject,
                'date': date,
                'from': from_email,
                'content': content
            }
        except Exception as e:
            st.error(f"Fehler beim Lesen der E-Mail-Details: {e}")
            return None

    def generate_response(self, email_content: str) -> str:
        """
        Uses the new Anthropic Client to generate a short, warm, empathetic reply.
        We'll do a simple completion request with HUMAN/AI prompts.
        """
        try:
            # Build the prompt using Anthropic's HUMAN_PROMPT / AI_PROMPT tokens
            prompt = (
                anthropic.HUMAN_PROMPT
                + f"Bitte lies diese E-Mail:\n\n{email_content}\n\n"
                + "Schreibe eine empathische und warmherzige Antwort (4-5 Sätze)."
                + anthropic.AI_PROMPT
            )

            response = self.claude.completions.create(
                model="claude-2",  # or whichever Claude model is available
                prompt=prompt,
                max_tokens_to_sample=300,
                temperature=0.7
            )
            # 'response' is a dict with a 'completion' field
            return response.completion.strip()

        except Exception as e:
            st.error(f"Fehler bei der Antwortgenerierung: {e}")
            return ""

    def get_subject(self, msg_id: str) -> str:
        """
        Retrieves only the subject header from the email (metadata format).
        """
        try:
            message = self.service.users().messages().get(
                userId='me', 
                id=msg_id, 
                format='metadata', 
                metadataHeaders=['subject']
            ).execute()

            headers = message['payload'].get('headers', [])
            subject = next((header['value'] for header in headers 
                            if header['name'].lower() == 'subject'), 'Keine Betreffzeile')
            return subject
        except Exception as e:
            st.error(f"Fehler beim Lesen des Betreffs: {e}")
            return 'Fehler beim Lesen des Betreffs'

    def send_response(self, msg_id: str, response_text: str) -> bool:
        """
        Sends the generated response email and marks the original as READ.
        """
        try:
            original_subject = self.get_subject(msg_id)
            final_subject = sanitize_subject(original_subject)

            message = MIMEText(response_text)
            message['to'] = self.target_email
            message['subject'] = final_subject

            raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
            self.service.users().messages().send(
                userId='me',
                body={'raw': raw, 'threadId': msg_id}
            ).execute()

            # Mark the original message as READ
            self.service.users().messages().modify(
                userId='me',
                id=msg_id,
                body={'removeLabelIds': ['UNREAD']}
            ).execute()

            return True
        except Exception as e:
            st.error(f"Fehler beim Senden der Antwort: {e}")
            return False

# ====================
# Streamlit UI Logic
# ====================

def main():
    st.set_page_config(page_title="E-Mail Bot", page_icon="✉️", layout="wide")

    # Verify that the Gmail token is available in secrets
    if 'gmail_token' not in st.secrets:
        st.error("""
        Bitte fügen Sie den Gmail-Token zu Ihren Streamlit Secrets hinzu.

        So generieren Sie den Token (lokal mit OAuth Playground):
        1. Besuchen Sie https://developers.google.com/oauthplayground
        2. Wählen Sie 'Gmail API v1' und den Scope 'https://www.googleapis.com/auth/gmail.modify'
        3. Autorisieren Sie den Zugriff
        4. Kopieren Sie den generierten Token in Ihre secrets.toml:

        [gmail_token]
        token = "..."
        refresh_token = "..."
        token_uri = "https://oauth2.googleapis.com/token"
        client_id = "..."
        client_secret = "..."
        scopes = ["https://www.googleapis.com/auth/gmail.modify"]
        """)
        st.stop()

    apply_custom_css()
    initialize_session_state()

    # Sidebar
    with st.sidebar:
        st.header("🤖 E-Mail Bot Status")
        st.write(f"**Aktiver Account:** {st.secrets['gmail_sender']}")
        st.write(f"**Ziel E-Mail:** {st.secrets['gmail_target']}")
        st.divider()

        # Monitoring Controls
        st.header("🔄 Monitoring")
        check_interval = st.secrets.get("config", {}).get("check_interval", 5)
        st.write(f"**Prüfintervall:** {check_interval} Minuten")

        if not st.session_state.is_monitoring:
            if st.button("▶️ Monitoring starten", use_container_width=True):
                st.session_state.is_monitoring = True
                st.experimental_rerun()
        else:
            if st.button("⏹️ Monitoring stoppen", use_container_width=True):
                st.session_state.is_monitoring = False
                st.experimental_rerun()

        if st.session_state.last_check:
            st.info(f"Letzte Prüfung: {st.session_state.last_check.strftime('%H:%M:%S')}")

    # Main Layout
    col1, col2 = st.columns([1, 1])

    # Initialize the bot
    bot = EmailBot()

    # Left column: incoming emails
    with col1:
        st.header("📨 Eingehende E-Mails")

        if st.session_state.is_monitoring:
            unread = bot.get_unread_emails()

            if unread:
                for email_msg in unread:
                    email_details = bot.get_email_details(email_msg['id'])
                    if email_details:
                        with st.container():
                            st.subheader(f"📧 {email_details['subject']}")
                            st.caption(f"Von: {email_details['from']}")
                            st.caption(f"Datum: {email_details['date']}")
                            st.markdown("---")
                            st.markdown(email_details['content'])

                            # Button to generate a response
                            if st.button(
                                f"Antwort generieren für '{email_details['subject']}'",
                                key=email_details['id']
                            ):
                                st.session_state.current_email = email_details
                                response = bot.generate_response(email_details['content'])
                                st.session_state.current_response = response
                                st.experimental_rerun()
            else:
                st.info("📭 Keine neuen E-Mails")

            # Update last_check timestamp
            st.session_state.last_check = datetime.now()

            # Automatic refresh (blocking approach)
            if st.session_state.is_monitoring:
                time.sleep(check_interval * 60)
                st.experimental_rerun()
        else:
            st.info("⏸️ Monitoring pausiert")

    # Right column: response panel
    with col2:
        st.header("✍️ KI-Antwortvorschlag")
        if st.session_state.current_email and st.session_state.current_response:
            with st.container():
                st.subheader(f"Re: {st.session_state.current_email['subject']}")
                st.markdown("---")
                st.markdown(st.session_state.current_response)

                colA, colB = st.columns(2)
                with colA:
                    if st.button("✉️ Antwort senden", type="primary", use_container_width=True):
                        success = bot.send_response(
                            st.session_state.current_email['id'], 
                            st.session_state.current_response
                        )
                        if success:
                            st.success("Antwort erfolgreich gesendet!")
                            # Add to email history
                            st.session_state.email_history.append({
                                'time': datetime.now(),
                                'email': st.session_state.current_email,
                                'response': st.session_state.current_response
                            })
                            # Reset current email/response
                            st.session_state.current_email = None
                            st.session_state.current_response = None
                            st.experimental_rerun()

                with colB:
                    if st.button("🔄 Neue Antwort generieren", use_container_width=True):
                        response = bot.generate_response(st.session_state.current_email['content'])
                        st.session_state.current_response = response
                        st.experimental_rerun()
        else:
            st.info("Wählen Sie eine E-Mail aus, um einen Antwortvorschlag zu generieren.")

        # Email history
        if st.session_state.email_history:
            st.divider()
            st.header("📋 Letzte Antworten")

            max_history = st.secrets.get("config", {}).get("max_history", 5)
            for entry in reversed(st.session_state.email_history[-max_history:]):
                with st.expander(f"📧 {entry['email']['subject']} ({entry['time'].strftime('%H:%M:%S')})"):
                    st.caption(f"Von: {entry['email']['from']}")
                    st.caption(f"Datum: {entry['email']['date']}")
                    colA, colB = st.columns(2)
                    with colA:
                        st.markdown("**Original E-Mail:**")
                        st.markdown(entry['email']['content'])
                    with colB:
                        st.markdown("**Gesendete Antwort:**")
                        st.markdown(entry['response'])

if __name__ == "__main__":
    main()
