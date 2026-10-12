import re
import secrets
from datetime import timedelta

from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.contrib import messages as flash
from django.contrib.auth import login, logout
from django.contrib.auth.models import User
from django.db.models import Q
from django.utils import timezone
from django.utils.crypto import salted_hmac, constant_time_compare
from django.db.models import Q, Count
from .models import Message, UserStatus, Profile, PhoneOTP


# =====================================================
#  HOME: sirf unki list jinse baat hui hai
# =====================================================

def home(request):

    if not request.user.is_authenticated:
        return redirect("login")

    # Sirf wahi log jinse meri baat hui hai
    chatted_ids = set(
        Message.objects.filter(sender_user=request.user)
        .values_list("receiver_user_id", flat=True)
    ) | set(
        Message.objects.filter(receiver_user=request.user)
        .values_list("sender_user_id", flat=True)
    )
    chatted_ids.discard(None)
    chatted_ids.discard(request.user.id)

    users = list(User.objects.filter(id__in=chatted_ids))

    # Kis dost ke kitne messages maine abhi tak nahi dekhe
    unread_map = dict(
        Message.objects.filter(receiver_user=request.user, seen_at__isnull=True)
        .values_list("sender_user")
        .annotate(c=Count("id"))
    )

    # Har user ke saath mera last message
    for u in users:
        u.unread = unread_map.get(u.id, 0)
        u.last_message = Message.objects.filter(
            Q(sender_user=request.user, receiver_user=u) |
            Q(sender_user=u, receiver_user=request.user)
        ).order_by("-created_at").first()

    # Jisse abhi baat hui wo upar
    users.sort(key=lambda u: u.last_message.created_at, reverse=True)

    return render(request, "index.html", {"users": users})


# =====================================================
#  NEW CHAT: phone number se chat shuru karna
# =====================================================

def new_chat(request):

    if not request.user.is_authenticated:
        return redirect("login")

    phone = request.GET.get("phone", "").strip()

    if not re.fullmatch(r"[6-9]\d{9}", phone):
        flash.error(request, "Enter a valid 10-digit mobile number.")
        return redirect("home")

    profile = Profile.objects.filter(phone=phone).select_related("user").first()

    if not profile:
        flash.error(request, "This number is not on HelloApp yet.")
        return redirect("home")

    if profile.user_id == request.user.id:
        flash.error(request, "That is your own number.")
        return redirect("home")

    return redirect("chat_page", user_id=profile.user_id)


# =====================================================
#  CHAT PAGE
# =====================================================

def chat_page(request, user_id):

    if not request.user.is_authenticated:
        return redirect("login")

    receiver = get_object_or_404(
        User,
        id=user_id
    )

    messages = Message.objects.filter(
        sender_user=request.user,
        receiver_user=receiver
    ) | Message.objects.filter(
        sender_user=receiver,
        receiver_user=request.user
    )

    messages = messages.order_by("created_at")

    # Saamne wale ka last seen (agar kabhi offline hua hai)
    status = UserStatus.objects.filter(user=receiver).first()
    receiver_last_seen = (
        status.last_seen.isoformat()
        if status and status.last_seen
        else ""
    )

    return render(request, "chat.html", {
        "messages": messages,
        "receiver": receiver,
        "receiver_last_seen": receiver_last_seen,
    })


def send_message(request):

    if not request.user.is_authenticated:
        return JsonResponse({
            "status": "error",
            "message": "Login required"
        })

    if request.method == "POST":

        text = request.POST.get("message")
        receiver_id = request.POST.get("receiver_id")

        receiver = get_object_or_404(
            User,
            id=receiver_id
        )

        Message.objects.create(
            sender=request.user.username,
            sender_user=request.user,
            receiver_user=receiver,
            text=text
        )

        return JsonResponse({
            "status": "success"
        })

    return JsonResponse({
        "status": "error"
    })


def logout_view(request):

    logout(request)

    return redirect("login")


# =====================================================
#  WHATSAPP JAISA LOGIN / REGISTER (phone + OTP)
# =====================================================

def make_hash(phone, code):
    # OTP ko scramble karta hai, asli OTP kahin save nahi hota
    return salted_hmac("hello-otp", f"{phone}:{code}").hexdigest()


def send_otp(phone, code):
    # DEVELOPMENT: OTP terminal mein print hota hai.
    # Baad mein asli SMS bhejna ho toh bas yahin SMS provider ka code lagega.
    print(f"\n========== OTP for +91 {phone} : {code} ==========\n")


def welcome_view(request):
    if request.user.is_authenticated:
        return redirect("home")
    return render(request, "welcome.html")


def phone_view(request):

    if request.user.is_authenticated:
        return redirect("home")

    error = None

    if request.method == "POST":
        phone = request.POST.get("phone", "").strip()

        if not re.fullmatch(r"[6-9]\d{9}", phone):
            error = "Enter a valid 10-digit mobile number."
        elif PhoneOTP.objects.filter(
            phone=phone,
            created_at__gte=timezone.now() - timedelta(seconds=30)
        ).exists():
            error = "Please wait 30 seconds before requesting a new code."
        else:
            # purane OTP bekaar karo, naya banao
            PhoneOTP.objects.filter(phone=phone, used=False).update(used=True)
            code = f"{secrets.randbelow(1000000):06d}"
            PhoneOTP.objects.create(phone=phone, code_hash=make_hash(phone, code))
            send_otp(phone, code)
            request.session["otp_phone"] = phone
            return redirect("register_otp")

    return render(request, "register_phone.html", {"error": error})


def otp_view(request):

    phone = request.session.get("otp_phone")
    if not phone:
        return redirect("register_phone")

    masked = phone[:2] + "••••••" + phone[-2:]
    error = None

    if request.method == "POST":
        code = request.POST.get("otp", "").strip()
        otp = PhoneOTP.objects.filter(phone=phone, used=False).order_by("-created_at").first()

        if not otp or timezone.now() - otp.created_at > timedelta(minutes=5):
            error = "This code has expired. Please request a new one."
        elif otp.attempts >= 5:
            error = "Too many wrong attempts. Please request a new code."
        elif not constant_time_compare(otp.code_hash, make_hash(phone, code)):
            otp.attempts += 1
            otp.save()
            error = "Incorrect code. Please try again."
        else:
            otp.used = True
            otp.save()
            request.session.pop("otp_phone", None)

            # Purana user hai toh seedha login
            profile = Profile.objects.filter(phone=phone).select_related("user").first()
            if profile:
                login(request, profile.user)
                return redirect("home")

            # Naya user hai toh naam aur DP maango
            request.session["verified_phone"] = phone
            return redirect("register_profile")

    return render(request, "register_otp.html", {"error": error, "masked_phone": masked})


def profile_view(request):

    phone = request.session.get("verified_phone")
    if not phone:
        return redirect("register_phone")

    error = None

    if request.method == "POST":
        name = request.POST.get("full_name", "").strip()
        dp = request.FILES.get("dp")

        if not name:
            error = "Please enter your name."
        elif dp and not dp.content_type.startswith("image/"):
            error = "Please upload an image file."
        elif dp and dp.size > 2 * 1024 * 1024:
            error = "Photo must be smaller than 2 MB."
        else:
            # username = phone number, password nahi (unusable)
            user = User.objects.create_user(username=phone)
            Profile.objects.create(user=user, phone=phone, full_name=name, dp=dp)
            request.session.pop("verified_phone", None)
            login(request, user)
            return redirect("home")

    return render(request, "register_profile.html", {"error": error})