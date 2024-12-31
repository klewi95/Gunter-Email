import streamlit as st
import os
import base64
from email.mime.text import MIMEText
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import anthropic
import time
from datetime import datetime

class EmailBot:
    def __init__(self):
        self.SCOPES = ['https://www.googleapis.com/auth/gmail.modify']
        self.sender_email = st.secrets["gmail_sender"]
        self.target_email = st.secrets["gmail_target"]
        self.service = self.setup_gmail()
        self.claude = anthropic.Anthropic(api_key=st.secrets["claude_api_key"])
        
    def setup_gmail(self):
        """Gmail API Setup mit Session State Token-Speicherung"""
        creds = None
        
        # Token aus Session State laden
        if 'gmail_token' in st.session_state:
            creds = Credentials.from_authorized_user_info(st.session_state.gmail_token, self.SCOPES)
            
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    'credentials.json', 
                    self.SCOPES
                )
                creds = flow.run_local_server(port=0)
                
            # Token in Session State speichern
            st.session_state.gmail_token = {
                'token': creds.token,
                'refresh_token': creds.refresh_token,
                'token_uri': creds.token_uri,
                'client_id': creds.client_id,
                'client_secret': creds.client_secret,
                'scopes': creds.scopes
            }
            
        return build('gmail', 'v1', credentials=creds)

    def get_unread_emails(self):
        """Holt ungelesene E-Mails von Günter"""
        query = f'from:{self.target_email} is:unread'
        try:
            results = self.service.users().messages().list(userId='me', q=query).execute()
            return results.get('messages', [])
        except Exception as e:
            st.error(f"Fehler beim Abrufen der E-Mails: {e}")
            return []

    def get_email_content(self, msg_id):
        """Extrahiert den Inhalt einer E-Mail"""
        try:
            message = self.service.users().messages().get(userId='me', id=msg_id, format='full').execute()
            
            if 'payload' in message:
                if 'parts' in message['payload']:
                    for part in message['payload']['parts']:
                        if part['mimeType'] == 'text/plain':
                            return base64.urlsafe_b64decode(part['body']['data']).decode()
                elif 'body' in message['payload']:
                    return base64.urlsafe_b64decode(message['payload']['body']['data']).decode()
            return ""
        except Exception as e:
            st.error(f"Fehler beim Lesen der E-Mail: {e}")
            return ""

    def get_email_details(self, msg_id):
        """Holt alle Details einer E-Mail"""
        try:
            message = self.service.users().messages().get(
                userId='me', 
                id=msg_id, 
                format='full'
            ).execute()
            
            # Betreff und andere Header extrahieren
            headers = message['payload']['headers']
            subject = next((h['value'] for h in headers if h['name'].lower() == 'subject'), 'Kein Betreff')
            date = next((h['value'] for h in headers if h['name'].lower() == 'date'), 'Kein Datum')
            from_email = next((h['value'] for h in headers if h['name'].lower() == 'from'), 'Unbekannter Absender')
            
            # Inhalt extrahieren
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

    def generate_response(self, email_content):
        """Generiert eine Antwort mit Claude"""
        try:
            message = self.claude.messages.create(
                model="claude-3-haiku-20240307",
                max_tokens=300,
                temperature=0.7,
                system="Du bist ein empathischer E-Mail-Assistent, der persönliche und warmherzige Antworten verfasst.",
                messages=[{
                    "role": "user", 
                    "content": f"""
                    Lies die folgende E-Mail und generiere eine herzliche, persönliche Antwort. 
                    Die Antwort sollte:
                    - Empathisch und warm sein
                    - Auf spezifische Details aus der E-Mail eingehen
                    - Interesse an den geteilten Erlebnissen zeigen
                    - Nicht zu lang sein (max. 4-5 Sätze)
                    - In einem natürlichen, konversationellen Ton geschrieben sein
                    
                    E-Mail-Inhalt:
                    {email_content}
                    """
                }]
            )
            return message.content
        except Exception as e:
            st.error(f"Fehler bei der Antwortgenerierung: {e}")
            return ""

    def send_response(self, msg_id, response_text):
        """Sendet die generierte Antwort"""
        try:
            message = MIMEText(response_text)
            message['to'] = self.target_email
            message['subject'] = 'Re: ' + self.get_subject(msg_id)
            
            raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
            self.service.users().messages().send(
                userId='me',
                body={'raw': raw, 'threadId': msg_id}
            ).execute()
            
            # E-Mail als gelesen markieren
            self.service.users().messages().modify(
                userId='me',
                id=msg_id,
                body={'removeLabelIds': ['UNREAD']}
            ).execute()
            
            return True
        except Exception as e:
            st.error(f"Fehler beim Senden der Antwort: {e}")
            return False

    def get_subject(self, msg_id):
        """Holt den Betreff einer E-Mail"""
        try:
            message = self.service.users().messages().get(
                userId='me', 
                id=msg_id, 
                format='metadata', 
                metadataHeaders=['subject']
            ).execute()
            
            headers = message['payload']['headers']
            for header in headers:
                if header['name'].lower() == 'subject':
                    return header['value']
            return 'Keine Betreffzeile'
        except Exception as e:
            st.error(f"Fehler beim Lesen des Betreffs: {e}")
            return 'Fehler beim Lesen des Betreffs'

def initialize_session_state():
    """Initialisiert die Session State Variablen"""
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
    """Wendet benutzerdefiniertes CSS an"""
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

def main():
    st.set_page_config(
        page_title="E-Mail Bot",
        page_icon="✉️",
        layout="wide"
    )
    
    apply_custom_css()
    initialize_session_state()
    
    # Sidebar für Monitoring-Steuerung
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
    
    # Hauptbereich aufteilen
    col1, col2 = st.columns([1, 1])
    
    # E-Mail-Bot initialisieren
    bot = EmailBot()
    
    # Linke Spalte: E-Mail-Anzeige
    with col1:
        st.header("📨 Eingehende E-Mails")
        
        # Neue E-Mails abrufen und anzeigen
        if st.session_state.is_monitoring:
            unread = bot.get_unread_emails()
            
            if unread:
                for email_msg in unread:
                    email_details = bot.get_email_details(email_msg['id'])
                    if email_details:
                        with st.container(border=True):
                            st.subheader(f"📧 {email_details['subject']}")
                            st.caption(f"Von: {email_details['from']}")
                            st.caption(f"Datum: {email_details['date']}")
                            st.markdown("---")
                            st.markdown(email_details['content'])
                            
                            # Button zum Generieren einer Antwort
                            if st.button(f"Antwort generieren für '{email_details['subject']}'"):
                                st.session_state.current_email = email_details
                                response = bot.generate_response(email_details['content'])
                                st.session_state.current_response = response
                                st.experimental_rerun()
            else:
                st.info("📭 Keine neuen E-Mails")
                
            # Zeitpunkt aktualisieren
            st.session_state.last_check = datetime.now()
            
            # Automatisches Neuladen
            if st.session_state.is_monitoring:
                time.sleep(check_interval * 60)
                st.experimental_rerun()
        else:
            st.info("⏸️ Monitoring pausiert")
    
    # Rechte Spalte: Antwort-Anzeige
    with col2:
        st.header("✍️ KI-Antwortvorschlag")
        
        if st.session_state.current_email and st.session_state.current_response:
            with st.container(border=True):
                st.subheader(f"Re: {st.session_state.current_email['subject']}")
                st.markdown("---")
                st.markdown(st.session_state.current_response)
                
                col1, col2 = st.columns(2)
                with col1:
                    if st.button("✉️ Antwort senden", type="primary", use_container_width=True):
                        if bot.send_response(st.session_state.current_email['id'], 
                                          st.session_state.current_response):
                            st.success("Antwort erfolgreich gesendet!")
                            # E-Mail zur Historie hinzufügen
                            st.session_state.email_history.append({
                                'time': datetime.now(),
                                'email': st.session_state.current_email,
                                'response': st.session_state.current_response
                            })
                            # Aktuelle E-Mail zurücksetzen
                            st.session_state.current_email = None
                            st.session_state.current_response = None
                            st.experimental_rerun()
                
                with col2:
                    if st.button("🔄 Neue Antwort generieren", use_container_width=True):
                        response = bot.generate_response(st.session_state.current_email['content'])
                        st.session_state.current_response = response
                        st.experimental_rerun()
        else:
            st.info("Wählen Sie eine E-Mail aus, um einen Antwortvorschlag zu generieren.")
            
        # E-Mail-Historie
        if st.session_state.email_history:
            st.divider()
            st.header("📋 Letzte Antworten")
            max_history = st.secrets.get("config", {}).get("max_history", 5)
            for entry in reversed(st.session_state.email_history[-max_history:]):
                with st.expander(f"📧 {entry['email']['subject']} ({entry['time'].strftime('%H:%M:%S')})"):
                    st.caption(f"Von: {entry['email']['from']}")
                    st.caption(f"Datum: {entry['email']['date']}")
                    
                    col1, col2 = st.columns(2)
                    with col1:
                        st.markdown("**Original E-Mail:**")
                        st.markdown(entry['email']['content'])
                    with col2:
                        st.markdown("**Gesendete Antwort:**")
                        st.markdown(entry['response'])

if __name__ == "__main__":
    main()
