import os
from flask import Flask, render_template, redirect, url_for, request, jsonify
from flask_socketio import SocketIO
from PIL import Image
from captcha.image import ImageCaptcha
from datetime import datetime
from pymongo import MongoClient
import secrets
import random
import smtplib
import io
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import base64
import socket
import hashlib
import string

client = MongoClient("mongodb://localhost:27017")
db = client["Chatting_App"]
logs = db["server_logs"]
users = db["User_credentials"]

sessions__ = {}

class CaptchaFunctions:
    @staticmethod
    def validate_captcha(captcha_e, captcha):
        if captcha_e == captcha:
            return True
        if captcha_e != captcha:
            return False

    @staticmethod
    def create_captcha(width=200, height=80):
        captcha_text = ''.join(random.choices('ABCDEFGHIJKLMNOPQRSTUVWXYZ23456789', k=4))
        return captcha_text

    @staticmethod
    def generate_captcha_image(captcha_text):
        image_captcha = ImageCaptcha(width=250, height=70)
        image_io = io.BytesIO()
        image_captcha.write(captcha_text, image_io, format='PNG')
        image_io.seek(0)

        image_base64 = base64.b64encode(image_io.read()).decode("utf-8")

        return image_base64

class EmailFunctions:
    def custom_email(to_email, body, subject):
        email = to_email
        message = MIMEMultipart()
        sender_email = "noreply.lanchatting@gmail.com"
        app_password = os.environ.get("SMTP_PASSWORD", "")

        body = body

        recipient_email = email
        message["From"] = sender_email
        message["To"] = recipient_email
        message["Subject"] = subject

        # Attach the body of the email
        message.attach(MIMEText(body, "plain"))

        # Connect to the SMTP server
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            # Start TLS for security
            server.starttls()

            # Login with your app-specific password
            server.login(sender_email, app_password)

            # Send the email
            server.sendmail(sender_email, recipient_email, message.as_string())
        return
        
    def generate_random_string(length):
        # Define the characters to choose from
        characters = string.ascii_uppercase + string.digits

        # Generate random string
        random_string = ''.join(random.choice(characters) for _ in range(length))

        return random_string

    @staticmethod
    def send_otp(email, session_id, for_type, ip='None'):
        email = email
        message = MIMEMultipart()
        sender_email = "noreply.lanchatting@gmail.com"
        app_password = os.environ.get("SMTP_PASSWORD", "")

        subject = "Verify your email address for SyncSphere"
        otp = str(random.randint(100000, 999999))

        #functions.update_dict(object_=session_id, uattris={'otp': otp, 'is_otp_validated': False})
        sessions__[session_id] = {'otp': otp}

        body = ''

        if for_type == 'update_pass':
            body = "OTP to verify your email address you just entered to update password for your account on SyncSphere is: " + otp + "\nThis OTP can be used only one time, and will be invalid after use.\nThis OTP was requested by a device running on IP Address : '" + ip + "'. If the IP address is seen as 'None', the requesting device was probably using a VPN. Contact us for any needed assistance.\n\n\nRegards,\nSyncSphere developers team."

        if for_type == 'register':
            body = "OTP to verify your email address you just entered to create an account in SyncSphere is: " + otp + "\nThis OTP can be used only one time, and will be invalid after use.\nThis OTP was requested by a device running on IP Address : '" + ip + "'. If the IP address is seen as 'None', the requesting device was probably using a VPN. Contact us for any needed assistance.\n\n\nThanks for opting into our app 😊\n\nRegards,\nSyncSphere developers team."

        recipient_email = email
        message["From"] = sender_email
        message["To"] = recipient_email
        message["Subject"] = subject

        # Attach the body of the email
        message.attach(MIMEText(body, "plain"))

        # Connect to the SMTP server
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            # Start TLS for security
            server.starttls()

            # Login with your app-specific password
            server.login(sender_email, app_password)

            # Send the email
            server.sendmail(sender_email, recipient_email, message.as_string())
        return
    
    @staticmethod
    def send_support(user, issue, email_u):
        email = ['1032211551@mitwpu.edu.in']
        message = MIMEMultipart()
        message_u = MIMEMultipart()
        sender_email = "noreply.lanchatting@gmail.com"
        app_password = os.environ.get("SMTP_PASSWORD", "")
        report_ticket = EmailFunctions.generate_random_string(10)

        subject = "User wanted some help!"

        body = f"Hello SyncSphere Developers!\nThe user '{user}' needs your help. They have this issue with our system:\n==========\n{issue}\n==========\nThey have left their email: {email_u} ."
        
        subject_u = "Regarding your latest issue report"

        body_u = f"Hello SyncSphere user '{user}',\nWe have reported your issue\n==========\n{issue}\n==========\nOur developers are looking into the issue and will revert back on this email soon.\nWe are extremely sorry for any inconvinience caused.\nKeep this report ticket handy:\n{report_ticket}\nRegards,\nSyncSphere."

        recipient_email = email
        message["From"] = sender_email
        message["To"] = ", ".join(recipient_email)
        message["Subject"] = subject
        
        recipient_email_u = email_u
        message_u["From"] = sender_email
        message_u["To"] = recipient_email_u
        message_u["Subject"] = subject_u
        
        # Attach the body of the email
        message.attach(MIMEText(body, "plain"))
        
        message_u.attach(MIMEText(body_u, "plain"))

        # Connect to the SMTP server
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            # Start TLS for security
            server.starttls()

            # Login with your app-specific password
            server.login(sender_email, app_password)

            # Send the email
            server.sendmail(sender_email, recipient_email, message.as_string())
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            # Start TLS for security
            server.starttls()

            # Login with your app-specific password
            server.login(sender_email, app_password)

            # Send the email
            server.sendmail(sender_email, recipient_email_u, message_u.as_string())
        return

    @staticmethod
    def validate_otp(session_id, otpv):
        otpv = otpv
        #otp = functions.get_value(object_=session_id, attribute='otp')
        otp = sessions__[session_id]['otp']

        try:
            if otpv == otp:
                #functions.update_dict(object_=session_id, uattris={'otp': None, 'is_otp_validated': True})
                return 'correct'
            if otpv != otp:
                return 'incorrect'
        except Exception as e:
            print(f"\033[41m\033[1mSome error occured. Error: {e}\033[0m")
            return f'False'

    @staticmethod
    def send_thank_you(email_, user_):
        email = email_
        message = MIMEMultipart()
        sender_email = "noreply.lanchatting@gmail.com"
        app_password = os.environ.get("SMTP_PASSWORD", "")

        subject = "Account Registration Successfull!😊"

        body = f"Hello {user_},\n       Your account was successfully registered at SyncSphere!!😄 Enjoy the experience of chatting, with SyncSphere!!😎 Contact us for any needed assistance🤝.\n\n\nThanks for opting into our app 😊\n\nRegards,\nSyncSphere developers team."

        recipient_email = email
        message["From"] = sender_email
        message["To"] = recipient_email
        message["Subject"] = subject

        # Attach the body of the email
        message.attach(MIMEText(body, "plain"))

        # Connect to the SMTP server
        #print(f"\033[41m\033[1mSending email..\033[0m")
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            # Start TLS for security
            server.starttls()

            # Login with your app-specific password
            server.login(sender_email, app_password)

            # Send the email
            server.sendmail(sender_email, recipient_email, message.as_string())
        #print(f"\033[41m\033[1mSent!\033[0m")
        #functions.server_logs(f'Account registration successfull for email ID "{email}". Confirmation email sent successfully to the mentioned email ID.')
        return

    @staticmethod
    def send_updation(email_, user_, ip="None"):
        email = email_
        message = MIMEMultipart()
        sender_email = "noreply.lanchatting@gmail.com"
        app_password = os.environ.get("SMTP_PASSWORD", "")

        subject = "IMPORTANT!!"

        body = f"Hello {user_},\n       Your password was recently changed by a device with IP address '{ip}'\nIf not done by you, please contact SyncSphere as soon as possible.\n\n(If you see IP as 'None', they were probably using a VPN)\n\n\nThanks for opting into our app 😊\n\nRegards,\nSyncSphere developers team."

        recipient_email = email
        message["From"] = sender_email
        message["To"] = recipient_email
        message["Subject"] = subject

        # Attach the body of the email
        message.attach(MIMEText(body, "plain"))

        # Connect to the SMTP server
        #print(f"\033[41m\033[1mSending email..\033[0m")
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            # Start TLS for security
            server.starttls()

            # Login with your app-specific password
            server.login(sender_email, app_password)

            # Send the email
            server.sendmail(sender_email, recipient_email, message.as_string())
        #print(f"\033[41m\033[1mSent!\033[0m")
        #functions.server_logs(f'Account registration successfull for email ID "{email}". Confirmation email sent successfully to the mentioned email ID.')
        return

class functions:
    @staticmethod
    def check_sid(sid):
        if sid in sessions__:
            return True
        else:
            return False

    @staticmethod 
    def register_user(data):
        user_id = data["usr"]
        email = data["eml"]
        password = data["psrd"]
        session_id = data["session_id"]
        current_datetime = datetime.now()
        formatted_datetime = current_datetime.strftime("%I:%M:%S %p %d/%m/%Y")
        #ip = request.remote_addr
        ip = data["IP"]

        valid_email = False
        if functions.get_value(object_=session_id, attribute='is_otp_validated') == True:
            valid_email = True

        users.insert_one({
            "username": user_id,
            "password": password,
            "email": email,
            "created at" : formatted_datetime,
            "created by IP address" : ip
        })

        if users.count_documents({"username": user_id}) != 0:
            return True
        else:
            return False

    @staticmethod
    def check_username(username):
        username = username
        existing_user = users.find_one({"username": username})
        if existing_user:
            return True
        else:
            return False

    @staticmethod
    def create_server_app():
        return Flask(__name__)

    @staticmethod
    def server_logs(data):
        current_datetime = datetime.now()
        formatted_datetime = current_datetime.strftime("%I:%M:%S %p %d/%m/%Y")
        logs.insert_one({
            "timestamp": formatted_datetime,
            "log_data": data
        })

    @staticmethod
    def create_socket(app):
        functions.server_logs('Server started')
        return SocketIO(app, manage_session=False)

    #@staticmethod
    #def create_session_id(ip):
        #current_datetime = datetime.now()
        #formatted_datetime = current_datetime.strftime('%H:%M:%S:%f-%d:%m:%Y')
#
        ##print(f'New session from IP "{ip}" at {formatted_datetime}.')
#
#
        #input_string = f'~!@#$%^&*()_+Yur//SESsiOn//IDsI={formatted_datetime}__ReQuesTed++FRoM:{ip}__.THanKU+_)(*&^%$#@!~'
        #hash_object = hashlib.new('sha-512')
        #hash_object.update(input_string.encode('utf-8'))
        #session_id = hash_object.hexdigest()
#
        #sessions__[session_id] = {'validity': False, 'IP': ip, 'otp': None, 'is_otp_validated': False, 'email': None, 'expired': True, 'captcha_text': None, 'captcha_image':None, 'login_session?': False, 'logged_in?': False, 'register_session?': False}
        #functions.server_logs(f'New session created for "{ip}". Session ID: {session_id}')
#
        #return session_id

    @staticmethod
    def create_session_id(ip):
        return secrets.token_urlsafe(32)

    @staticmethod
    def store_session(session_id, validity=False, ip=None, otp=None, otp_validated=False, expired=True, captcha_text=None, captcha_image=None, login_session=False, logged_in=False, register_session=False):
        try:
            sessions__[session_id] = {'validity': validity, 'IP':ip, 'otp': otp, 'is_otp_validated': otp_validated, 'expired': True, 'captcha_text': captcha_text, 'captcha_image': captcha_image, 'login_session?': login_session, 'logged_in?': logged_in, 'register_session?': register_session}
            return True
        except Exception as e:
            print(f'Error occured: {e}')
            return False

    @staticmethod
    def get_value(object_, attribute):
        object_ = object_
        if object_ in sessions__:
            return sessions__[object_].get(attribute)
        else:
            return None

    @staticmethod
    def get_public_ip():
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            public_ip = s.getsockname()[0]
            s.close()
            return public_ip
        except Exception as e:
            print(f"Error getting public IP: {e}")
            return None

    @staticmethod
    def get_dict():
        return sessions__

    @staticmethod
    def update_dict(object_, uattris):
        sessions__[object_].update(uattris)

    @staticmethod
    def get_captcha_image(object_):
        captcha_text = CaptchaFunctions.create_captcha()
        captcha_image = CaptchaFunctions.generate_captcha_image(captcha_text=captcha_text)
        functions.update_dict(object_=object_, uattris={'captcha_text': captcha_text, 'captcha_image': captcha_image})
        return captcha_image

    @staticmethod
    def check_user_details(userid, userpass, captcha_r, session_id):
        captcha_s = functions.get_value(object_=session_id, attribute='captcha_text')
        if captcha_r == captcha_s:
            existing_user = users.find_one({"username": userid})
            if existing_user:
                if existing_user["password"] == userpass:
                    return '86de98aad82f35fd56479e8469d0be93b76eaff117acf498b89b7d3b1893307e'
                else:
                    return '7306fa7a1d4f20e0888dc5c895f2c814bedd0bebef56ab3de92868051037a892'
            else:
                return 'a5ea03db8930f6f4f34b3604abeb050d990f4cf1176d55a50644238c65c9a4c9'
        else:
            return 'f3cd3c81850d221ad40affe53938b22627af7109e8092eb9b057699b6ada1f17'

    @staticmethod
    def delete_session(session_):
        if session_ in sessions__:
            del sessions__[session_]
            return True
        else:
            return False
