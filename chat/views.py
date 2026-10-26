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
from .models import Message, UserStatus, Profile, PhoneOTP, Status, StatusView
from PIL import Image
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer


from .consumers import message_status, build_events, room_name, user_card



#  HOME: sirf unki list jinse baat hui hai


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



#  NEW CHAT: phone number se chat shuru karna


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



#  CHAT PAGE


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
    
MAX_IMAGE_MB = 5
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "GIF"}


def upload_image(request):

    if not request.user.is_authenticated:
        return JsonResponse({"ok": False, "error": "Login required"}, status=401)

    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "POST only"}, status=405)

    # 1) Kisko bhej rahe hain
    try:
        receiver_id = int(request.POST.get("receiver_id", ""))
    except ValueError:
        return JsonResponse({"ok": False, "error": "Invalid receiver"}, status=400)

    receiver = User.objects.filter(id=receiver_id).first()
    if not receiver or receiver.id == request.user.id:
        return JsonResponse({"ok": False, "error": "Invalid receiver"}, status=400)

    # 2) File check: hai, size theek, sach mein photo
    file = request.FILES.get("image")
    if not file:
        return JsonResponse({"ok": False, "error": "No photo selected"}, status=400)

    if file.size > MAX_IMAGE_MB * 1024 * 1024:
        return JsonResponse({"ok": False, "error": f"Photo must be under {MAX_IMAGE_MB} MB"}, status=400)

    try:
        img = Image.open(file)
        image_format = img.format
        img.verify()                      # file andar se sach mein image hai ya nahi
    except Exception:
        return JsonResponse({"ok": False, "error": "This is not a valid image"}, status=400)

    if image_format not in ALLOWED_FORMATS:
        return JsonResponse({"ok": False, "error": "Only JPG, PNG, WEBP or GIF allowed"}, status=400)

    file.seek(0)                          # verify ke baad file ko shuru par laao

    # 3) Message save karo (sent / delivered / seen ka status tay karke)
    status = message_status(request.user.id, receiver.id)
    now = timezone.now()
    extra = {}
    if status in ("delivered", "seen"):
        extra["delivered_at"] = now
    if status == "seen":
        extra["seen_at"] = now

    msg = Message.objects.create(
        sender=request.user.username,
        sender_user=request.user,
        receiver_user=receiver,
        text=request.POST.get("caption", "").strip()[:1000],
        image=file,
        **extra
    )

    # 4) WebSocket se dono ko live khabar bhejo (text message jaisa hi)
    room_event, list_event = build_events(msg, status)
    send = async_to_sync(get_channel_layer().group_send)
    send(room_name(request.user.id, receiver.id), room_event)
    send(f"presence_user_{receiver.id}", list_event)
    send(f"presence_user_{request.user.id}", list_event)

    return JsonResponse({"ok": True, "id": msg.id})

#edit profile section yaha se jora hu

DEFAULT_ABOUT = "Hey there! I am using HelloApp"


def check_image(file, max_mb):
    """Photo sahi hai ya nahi dekho. Galti ho toh text deta hai, sahi ho toh None."""
    if file.size > max_mb * 1024 * 1024:
        return f"Photo must be under {max_mb} MB"

    try:
        img = Image.open(file)
        image_format = img.format
        img.verify()                      # file andar se sach mein image hai ya nahi
    except Exception:
        return "This is not a valid image"

    if image_format not in ALLOWED_FORMATS:
        return "Only JPG, PNG, WEBP or GIF allowed"

    file.seek(0)                          # verify ke baad file ko shuru par laao
    return None


def edit_profile(request):

    if not request.user.is_authenticated:
        return redirect("login")

    profile = Profile.objects.filter(user=request.user).first()

    # Profile nahi bana (jaise admin ya purana user): naya banane ka form dikhao
    creating = profile is None
    if creating:
        profile = Profile(user=request.user, about=DEFAULT_ABOUT)

    error = None
    name_value = profile.full_name
    about_value = profile.about
    phone_value = profile.phone

    if request.method == "POST":
        name_value = request.POST.get("full_name", "").strip()
        about_value = request.POST.get("about", "").strip()
        dp = request.FILES.get("dp")
        remove_dp = request.POST.get("remove_dp") == "on"

        if creating:
            phone_value = request.POST.get("phone", "").strip()

        if creating and not re.fullmatch(r"[6-9]\d{9}", phone_value):
            error = "Enter a valid 10-digit mobile number."
        elif creating and Profile.objects.filter(phone=phone_value).exists():
            error = "This number is already used by another account."
        elif not name_value:
            error = "Name cannot be empty."
        elif len(name_value) > 25:
            error = "Name can be at most 25 characters."
        elif len(about_value) > 140:
            error = "About can be at most 140 characters."
        else:
            error = check_image(dp, 2) if dp else None

        if not error:
            if creating:
                profile.phone = phone_value
            profile.full_name = name_value
            profile.about = about_value or DEFAULT_ABOUT

            if dp:
                if profile.dp:
                    profile.dp.delete(save=False)     # purani photo file bhi hatao
                profile.dp = dp
            elif remove_dp and profile.dp:
                profile.dp.delete(save=False)
                profile.dp = None

            profile.save()
            request.session["profile_saved"] = True
            return redirect("edit_profile")           # refresh par dobara save na ho

    saved = request.session.pop("profile_saved", False)

    return render(request, "edit_profile.html", {
        "profile": profile,
        "creating": creating,
        "error": error,
        "saved": saved,
        "name_value": name_value,
        "about_value": about_value,
        "phone_value": phone_value,
    })


def logout_view(request):

    logout(request)

    return redirect("login")



#  WHATSAPP JAISA LOGIN / REGISTER (phone + OTP)


def make_hash(phone, code):
    # OTP ko scramble karta hai, asli OTP kahin save nahi hota
    return salted_hmac("hello-otp", f"{phone}:{code}").hexdigest()


def send_otp(phone, code):
    # DEVELOPMENT: OTP terminal mein print hota hai.
    # Baad mein asli SMS bhejna ho toh bas yahin SMS provider ka code lagana prega .
    print(f"\n========== OTP for +91 {phone} : {code} ==========\n")


def welcome_view(request):
    if request.user.is_authenticated:
        return redirect("home")
    return render(request, "welcome.html")


def phone_view(request, mode="register"):

    if request.user.is_authenticated:
        return redirect("home")

    error = None

    if request.method == "POST":
        phone = request.POST.get("phone", "").strip()
        registered = Profile.objects.filter(phone=phone).exists()

        if not re.fullmatch(r"[6-9]\d{9}", phone):
            error = "Enter a valid 10-digit mobile number."
        elif mode == "login" and not registered:
            error = "This number is not registered yet. Please create a new account."
        elif mode == "register" and registered:
            error = "This number is already registered. Please log in."
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
            request.session["otp_mode"] = mode
            return redirect("register_otp")

    return render(request, "register_phone.html", {"error": error, "mode": mode})


def otp_view(request):

    phone = request.session.get("otp_phone")
    if not phone:
        return redirect("register_phone")

    masked = phone[:2] + "••••••" + phone[-2:]
    error = None

    # "Wrong number?" aur "Resend" kis page par wapas jayein
    mode = request.session.get("otp_mode", "register")
    back_url = "login" if mode == "login" else "register_phone"

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

    return render(request, "register_otp.html", {
        "error": error,
        "masked_phone": masked,
        "back_url": back_url,
    })
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

# =====================================================
#  STATUS (24 ghante wala)
# =====================================================

STATUS_HOURS = 24
STATUS_COLORS = ["#128C7E", "#075E54", "#6A1B9A", "#C62828", "#EF6C00", "#1565C0", "#37474F"]


def chat_partner_ids(user):
    """Wo log jinse mera message ka lena-dena hua hai (inhi ko mera status dikhta hai)."""
    ids = set(
        Message.objects.filter(sender_user=user).values_list("receiver_user_id", flat=True)
    ) | set(
        Message.objects.filter(receiver_user=user).values_list("sender_user_id", flat=True)
    )
    ids.discard(None)
    ids.discard(user.id)
    return ids


def active_statuses():
    """Sirf pichhle 24 ghante ke status."""
    return Status.objects.filter(created_at__gte=timezone.now() - timedelta(hours=STATUS_HOURS))


def delete_expired_statuses():
    """24 ghante se purane status (aur unki photo ki file) hata do."""
    cutoff = timezone.now() - timedelta(hours=STATUS_HOURS)
    for old in Status.objects.filter(created_at__lt=cutoff):
        if old.image:
            old.image.delete(save=False)
        old.delete()


def status_page(request):

    if not request.user.is_authenticated:
        return redirect("login")

    delete_expired_statuses()
    me = request.user

    error = None
    text_value = ""

    # ---- Naya status post karna
    if request.method == "POST":
        text_value = request.POST.get("text", "").strip()
        bg = request.POST.get("bg", "")
        image = request.FILES.get("image")

        if bg not in STATUS_COLORS:
            bg = STATUS_COLORS[0]

        if not text_value and not image:
            error = "Write something or choose a photo."
        elif len(text_value) > 300:
            error = "Status text can be at most 300 characters."
        else:
            error = check_image(image, 5) if image else None

        if not error:
            Status.objects.create(user=me, text=text_value, bg=bg, image=image)
            return redirect("status")

    # ---- Kiske status dikhane hain: mere apne + chat wale doston ke
    partners = chat_partner_ids(me)
    statuses = list(
        active_statuses()
        .filter(Q(user=me) | Q(user_id__in=partners))
        .select_related("user")
        .order_by("created_at")
    )

    viewed_ids = set(
        StatusView.objects.filter(viewer=me, status__in=statuses).values_list("status_id", flat=True)
    )

    # Mere status ko kis-kisne dekha
    viewers = {}
    for v in StatusView.objects.filter(status__user=me, status__in=statuses).select_related("viewer"):
        viewers.setdefault(v.status_id, []).append(user_card(v.viewer)[0])

    groups = {}
    for s in statuses:
        mine = s.user_id == me.id
        if s.user_id not in groups:
            name, dp = user_card(s.user)
            groups[s.user_id] = {"user_id": s.user_id, "name": "My status" if mine else name,
                                 "dp": dp, "mine": mine, "items": []}
        item = {
            "id": s.id,
            "text": s.text,
            "bg": s.bg,
            "image": s.image.url if s.image else "",
            "created_at": s.created_at.isoformat(),
            "viewed": mine or s.id in viewed_ids,
        }
        if mine:
            item["viewers"] = viewers.get(s.id, [])          # sirf owner ko dikhta hai
        groups[s.user_id]["items"].append(item)

    my_name, my_dp = user_card(me)
    if me.id not in groups:                                   # abhi koi status nahi
        groups[me.id] = {"user_id": me.id, "name": "My status", "dp": my_dp, "mine": True, "items": []}

    # Mera pehle. Baaki: jinka status maine nahi dekha wo pehle, phir naya-purana
    others = [g for g in groups.values() if not g["mine"]]
    others.sort(key=lambda g: g["items"][-1]["created_at"], reverse=True)      # naya upar
    others.sort(key=lambda g: all(i["viewed"] for i in g["items"]))             # unviewed pehle (stable sort)
    ordered = [groups[me.id]] + others

    return render(request, "status.html", {
        "status_data": {"me": me.id, "groups": ordered, "colors": STATUS_COLORS},
        "error": error,
        "text_value": text_value,
    })


def status_mark_viewed(request, status_id):

    if not request.user.is_authenticated:
        return JsonResponse({"ok": False, "error": "Login required"}, status=401)
    if request.method != "POST":
        return JsonResponse({"ok": False}, status=405)

    status = active_statuses().filter(id=status_id).first()
    if not status or status.user_id == request.user.id:
        return JsonResponse({"ok": False}, status=404)

    # Sirf wahi dekh sakta hai jisse mera chat ka rishta hai
    if status.user_id not in chat_partner_ids(request.user):
        return JsonResponse({"ok": False}, status=403)

    StatusView.objects.get_or_create(status=status, viewer=request.user)
    return JsonResponse({"ok": True})


def status_delete(request, status_id):

    if not request.user.is_authenticated:
        return JsonResponse({"ok": False, "error": "Login required"}, status=401)
    if request.method != "POST":
        return JsonResponse({"ok": False}, status=405)

    status = Status.objects.filter(id=status_id, user=request.user).first()   # sirf apna
    if not status:
        return JsonResponse({"ok": False}, status=404)

    if status.image:
        status.image.delete(save=False)
    status.delete()
    return JsonResponse({"ok": True})

def calls_page(request):
    if not request.user.is_authenticated:
        return redirect("login")
    return render(request, "calls.html")