"""
translations.py — all user-facing strings for Yaqadha (يقظة) in AR / FR / EN.

Design notes
------------
Message formatting IS the product's UI, so every string here is written to be
short, scannable, and consistent. We reuse a small, fixed set of emoji anchors:

    🔥  fire / heat alert
    📍  location
    ⚠️  disclaimer / caution
    ✅  confirmation / success
    ℹ️  info / status
    🕒  time

All messages use Telegram's **HTML** parse mode (not Markdown). HTML is far more
robust here because our text contains characters that break legacy Markdown —
most importantly the underscores in command names like ``/update_location``.
Bold is expressed with ``<b>…</b>``.

Use ``t(lang, key, **kwargs)`` to fetch a string; missing languages fall back to
Arabic, and ``{placeholders}`` are filled via ``str.format``. Any dynamic value
that could contain ``<``, ``>`` or ``&`` should be passed through :func:`esc`
first (the wilaya names and numbers we inject don't, but it's cheap insurance).
"""

from __future__ import annotations

import html

# Supported alert languages. Arabic is the default (Algeria's primary language)
# but the country is multilingual, so French and English are first-class too.
SUPPORTED_LANGUAGES = ("ar", "fr", "en")
DEFAULT_LANGUAGE = "ar"

# Human-readable language names, used in the /language menu.
LANGUAGE_NAMES = {
    "ar": "العربية",
    "fr": "Français",
    "en": "English",
}

# The Civil Protection emergency number in Algeria.
CIVIL_PROTECTION_NUMBER = "14"


def esc(value) -> str:
    """HTML-escape a dynamic value so it can't break the parse mode."""
    return html.escape(str(value), quote=False)


# ---------------------------------------------------------------------------
# String tables. Keep keys identical across all three languages.
# Bold uses <b>…</b> (HTML parse mode).
# ---------------------------------------------------------------------------

STRINGS = {
    "ar": {
        # ---- /start ----------------------------------------------------
        "welcome": (
            "🔥 <b>يقظة</b> — نظام تنبيه مبكر للحرائق في الجزائر\n\n"
            "أرصد بيانات الأقمار الاصطناعية وأنبّهك عند رصد "
            "بؤرة حرارية قرب موقعك.\n\n"
            "ℹ️ هذا تطبيق توعوي <b>لا يغني</b> عن الحماية المدنية. للطوارئ اتصل "
            "بالرقم <b>{civil}</b>.\n\n"
            "اختر إجراءً بالأسفل أو استعمل /help لعرض كل الأوامر."
        ),
        # ---- /help -----------------------------------------------------
        "help": (
            "ℹ️ <b>الأوامر المتاحة:</b>\n\n"
            "/subscribe — الاشتراك في التنبيهات (موقع أو ولاية)\n"
            "/update_location — تغيير موقعك المحفوظ\n"
            "/status — عرض اشتراكك وآخر فحص للبيانات\n"
            "/language — تغيير لغة التنبيهات\n"
            "/unsubscribe — إلغاء الاشتراك\n"
            "/help — عرض هذه القائمة\n\n"
            "⚠️ تنبيهاتي مبنية على رصد حراري بالأقمار الاصطناعية وقد تتأخر أو "
            "تحتوي إنذارات كاذبة. تحقّق ميدانيًا واتصل بالحماية المدنية على "
            "<b>{civil}</b>."
        ),
        # ---- subscription ---------------------------------------------
        "subscribe_prompt": (
            "📍 لتفعيل التنبيهات، شارك <b>موقعك الحالي</b> عبر الزر بالأسفل، "
            "أو اكتب اسم <b>ولايتك</b> بالعربية (مثال: تيزي وزو)."
        ),
        "share_location_button": "📍 مشاركة موقعي",
        "subscribed_gps": (
            "✅ تمّ تسجيلك بنجاح عند موقعك المحدّد.\n"
            "ستصلك تنبيهات فور رصد أي بؤرة حرارية قريبة (ضمن {radius} كم)."
        ),
        "subscribed_wilaya": (
            "✅ تمّ تسجيلك بنجاح في ولاية <b>{wilaya}</b>.\n"
            "ستصلك تنبيهات فور رصد أي بؤرة حرارية قريبة."
        ),
        "location_updated_gps": "✅ تمّ تحديث موقعك إلى نقطتك الحالية بنجاح.",
        "location_updated_wilaya": "✅ تمّ تحديث موقعك إلى ولاية <b>{wilaya}</b> بنجاح.",
        "wilaya_not_found": (
            "⚠️ لم أتعرّف على هذه الولاية. تأكّد من كتابة الاسم بالعربية "
            "(مثال: البليدة، وهران، سطيف) أو شارك موقعك مباشرة."
        ),
        "update_location_prompt": (
            "📍 أرسل <b>موقعك الجديد</b> عبر الزر بالأسفل، أو اكتب اسم "
            "<b>الولاية</b> الجديدة."
        ),
        "must_subscribe_first": (
            "ℹ️ لست مشتركًا بعد. استعمل /subscribe لتفعيل التنبيهات أولًا."
        ),
        # ---- unsubscribe ----------------------------------------------
        "unsubscribe_confirm": (
            "هل تريد فعلاً إلغاء اشتراكك؟ لن تصلك أي تنبيهات بعد ذلك."
        ),
        "unsubscribe_yes": "نعم، ألغِ الاشتراك",
        "unsubscribe_no": "تراجع",
        "unsubscribed": (
            "✅ تمّ إلغاء اشتراكك. يمكنك العودة في أي وقت عبر /subscribe. ابقَ آمنًا."
        ),
        "unsubscribe_cancelled": "👍 تمّ الإبقاء على اشتراكك.",
        "not_subscribed": "ℹ️ لست مشتركًا أصلًا.",
        # ---- status ----------------------------------------------------
        "status": (
            "ℹ️ <b>حالة اشتراكك</b>\n\n"
            "📍 الموقع: {location}\n"
            "🕒 تاريخ الاشتراك: {since}\n"
            "🛰️ آخر فحص ناجح للبيانات: {last_check}\n"
            "🗣️ لغة التنبيهات: {language}"
        ),
        "status_location_gps": "نقطة محدّدة ({lat:.3f}, {lon:.3f})",
        "last_check_never": "لم يتمّ بعد",
        # ---- language --------------------------------------------------
        "language_prompt": "🗣️ اختر لغة التنبيهات:",
        "language_set": "✅ تمّ ضبط لغة التنبيهات على <b>العربية</b>.",
        # ---- alerts ----------------------------------------------------
        "alert_single": (
            "🔥 <b>تنبيه حراري محتمل</b>\n\n"
            "📍 قرب <b>{place}</b> — <b>{distance} كم</b> {direction}\n"
            "🕒 {detected} بتوقيت الجزائر\n\n"
            "⚠️ هذا رصد حراري بالأقمار الاصطناعية وليس تأكيدًا لحريق، وقد يتأخّر "
            "ساعة أو أكثر. تحقّق ميدانيًا أو اتصل بالحماية المدنية على "
            "<b>{civil}</b> قبل اتخاذ أي إجراء."
        ),
        "alert_multi_header": "🔥 <b>تنبيه: {count} بؤر حرارية قرب موقعك</b> (بتوقيت الجزائر)\n",
        "alert_multi_item": "📍 قرب <b>{place}</b> — <b>{distance} كم</b> {direction} — 🕒 {detected}",
        "alert_more": "➕ و<b>{count}</b> بؤرة حرارية إضافية قريبة",
        "alert_multi_footer": (
            "\n⚠️ رصد حراري بالأقمار الاصطناعية قد يتأخّر أو يكون إنذارًا كاذبًا. "
            "تحقّق ميدانيًا واتصل بالحماية المدنية على <b>{civil}</b>."
        ),
        "alert_map_button": "🛰️ عرض على خريطة الأقمار الاصطناعية",
        # ---- channel ---------------------------------------------------
        "channel_post": (
            "🔥 <b>بؤرة حرارية جديدة</b> — قرب <b>{place}</b>\n"
            "📍 الإحداثيات: {lat:.4f}, {lon:.4f}\n"
            "🕒 {detected} بتوقيت الجزائر · درجة الثقة: {confidence}\n\n"
            "⚠️ رصد ساتلي غير مؤكّد. للطوارئ: <b>{civil}</b>"
        ),
        # ---- misc ------------------------------------------------------
        "unknown_command": "ℹ️ لم أفهم هذا الأمر. استعمل /help لعرض الأوامر.",
        "generic_error": "⚠️ حدث خطأ مؤقت. أعد المحاولة بعد قليل.",
        "btn_subscribe": "📍 اشترك",
        "btn_help": "ℹ️ مساعدة",
        "btn_language": "🗣️ English",
    },

    "fr": {
        "welcome": (
            "🔥 <b>Yaqadha</b> — alerte précoce des incendies en Algérie\n\n"
            "Je surveille les données satellites et vous "
            "préviens dès qu'un point de chaleur est détecté près de vous.\n\n"
            "ℹ️ Outil de sensibilisation, il <b>ne remplace pas</b> la "
            "Protection Civile. En cas d'urgence, appelez le <b>{civil}</b>.\n\n"
            "Choisissez une action ci-dessous ou tapez /help."
        ),
        "help": (
            "ℹ️ <b>Commandes disponibles :</b>\n\n"
            "/subscribe — s'abonner aux alertes (position ou wilaya)\n"
            "/update_location — modifier votre position\n"
            "/status — voir votre abonnement et le dernier relevé\n"
            "/language — changer la langue des alertes\n"
            "/unsubscribe — se désabonner\n"
            "/help — afficher ce menu\n\n"
            "⚠️ Mes alertes reposent sur une détection thermique satellite : "
            "possibles retards et fausses alertes. Vérifiez sur place et "
            "appelez la Protection Civile au <b>{civil}</b>."
        ),
        "subscribe_prompt": (
            "📍 Pour activer les alertes, partagez votre <b>position "
            "actuelle</b> via le bouton ci-dessous, ou tapez le nom de votre "
            "<b>wilaya</b> (ex. : Tizi Ouzou)."
        ),
        "share_location_button": "📍 Partager ma position",
        "subscribed_gps": (
            "✅ Inscription réussie à votre position.\n"
            "Vous serez alerté dès qu'un point de chaleur est détecté à "
            "proximité (dans un rayon de {radius} km)."
        ),
        "subscribed_wilaya": (
            "✅ Inscription réussie dans la wilaya de <b>{wilaya}</b>.\n"
            "Vous serez alerté dès qu'un point de chaleur proche est détecté."
        ),
        "location_updated_gps": "✅ Position mise à jour vers votre point actuel.",
        "location_updated_wilaya": "✅ Position mise à jour vers la wilaya de <b>{wilaya}</b>.",
        "wilaya_not_found": (
            "⚠️ Wilaya non reconnue. Vérifiez l'orthographe (ex. : Blida, Oran, "
            "Sétif) ou partagez directement votre position."
        ),
        "update_location_prompt": (
            "📍 Envoyez votre <b>nouvelle position</b> via le bouton, ou tapez "
            "le nom de la nouvelle <b>wilaya</b>."
        ),
        "must_subscribe_first": (
            "ℹ️ Vous n'êtes pas encore abonné. Utilisez d'abord /subscribe."
        ),
        "unsubscribe_confirm": (
            "Voulez-vous vraiment vous désabonner ? Vous ne recevrez plus d'alertes."
        ),
        "unsubscribe_yes": "Oui, me désabonner",
        "unsubscribe_no": "Annuler",
        "unsubscribed": (
            "✅ Désabonnement effectué. Revenez quand vous voulez via /subscribe. "
            "Restez prudent."
        ),
        "unsubscribe_cancelled": "👍 Votre abonnement est conservé.",
        "not_subscribed": "ℹ️ Vous n'êtes pas abonné.",
        "status": (
            "ℹ️ <b>État de votre abonnement</b>\n\n"
            "📍 Position : {location}\n"
            "🕒 Date d'inscription : {since}\n"
            "🛰️ Dernier relevé réussi : {last_check}\n"
            "🗣️ Langue des alertes : {language}"
        ),
        "status_location_gps": "point précis ({lat:.3f}, {lon:.3f})",
        "last_check_never": "pas encore effectué",
        "language_prompt": "🗣️ Choisissez la langue des alertes :",
        "language_set": "✅ Langue des alertes réglée sur <b>Français</b>.",
        "alert_single": (
            "🔥 <b>Alerte thermique possible</b>\n\n"
            "📍 près de <b>{place}</b> — <b>{distance} km</b> {direction}\n"
            "🕒 {detected} heure d'Algérie\n\n"
            "⚠️ Détection thermique satellite, <b>non confirmée</b>, pouvant "
            "être retardée d'une heure ou plus. Vérifiez sur place ou appelez "
            "la Protection Civile au <b>{civil}</b> avant toute action."
        ),
        "alert_multi_header": "🔥 <b>Alerte : {count} points de chaleur près de vous</b> (heure d'Algérie)\n",
        "alert_multi_item": "📍 près de <b>{place}</b> — <b>{distance} km</b> {direction} — 🕒 {detected}",
        "alert_more": "➕ et <b>{count}</b> autres points de chaleur à proximité",
        "alert_multi_footer": (
            "\n⚠️ Détection satellite possiblement retardée ou fausse. Vérifiez "
            "sur place et appelez la Protection Civile au <b>{civil}</b>."
        ),
        "alert_map_button": "🛰️ Voir sur la carte satellite",
        "channel_post": (
            "🔥 <b>Nouveau point de chaleur</b> — près de <b>{place}</b>\n"
            "📍 Coordonnées : {lat:.4f}, {lon:.4f}\n"
            "🕒 {detected} heure d'Algérie · confiance : {confidence}\n\n"
            "⚠️ Détection satellite non confirmée. Urgence : <b>{civil}</b>"
        ),
        "unknown_command": "ℹ️ Commande non reconnue. Tapez /help.",
        "generic_error": "⚠️ Une erreur temporaire est survenue. Réessayez.",
        "btn_subscribe": "📍 S'abonner",
        "btn_help": "ℹ️ Aide",
        "btn_language": "🗣️ العربية",
    },

    "en": {
        "welcome": (
            "🔥 <b>Yaqadha</b> — early wildfire alerts for Algeria\n\n"
            "I monitor satellite data and warn you when a heat "
            "hotspot is detected near your location.\n\n"
            "ℹ️ This is an awareness tool and <b>does not replace</b> Civil "
            "Protection. In an emergency call <b>{civil}</b>.\n\n"
            "Pick an action below or use /help for all commands."
        ),
        "help": (
            "ℹ️ <b>Available commands:</b>\n\n"
            "/subscribe — subscribe to alerts (location or wilaya)\n"
            "/update_location — change your saved location\n"
            "/status — view your subscription and last data check\n"
            "/language — change alert language\n"
            "/unsubscribe — stop alerts\n"
            "/help — show this menu\n\n"
            "⚠️ My alerts are based on satellite heat detection and may be "
            "delayed or false. Verify locally and call Civil Protection at "
            "<b>{civil}</b>."
        ),
        "subscribe_prompt": (
            "📍 To enable alerts, share your <b>current location</b> using the "
            "button below, or type your <b>wilaya</b> name (e.g. Tizi Ouzou)."
        ),
        "share_location_button": "📍 Share my location",
        "subscribed_gps": (
            "✅ You're subscribed at your pinned location.\n"
            "You'll be alerted whenever a heat hotspot is detected nearby "
            "(within {radius} km)."
        ),
        "subscribed_wilaya": (
            "✅ You're subscribed in <b>{wilaya}</b> wilaya.\n"
            "You'll be alerted whenever a nearby heat hotspot is detected."
        ),
        "location_updated_gps": "✅ Location updated to your current point.",
        "location_updated_wilaya": "✅ Location updated to <b>{wilaya}</b> wilaya.",
        "wilaya_not_found": (
            "⚠️ I didn't recognize that wilaya. Check the spelling (e.g. Blida, "
            "Oran, Setif) or just share your location."
        ),
        "update_location_prompt": (
            "📍 Send your <b>new location</b> using the button, or type the new "
            "<b>wilaya</b> name."
        ),
        "must_subscribe_first": (
            "ℹ️ You're not subscribed yet. Use /subscribe first."
        ),
        "unsubscribe_confirm": (
            "Are you sure you want to unsubscribe? You'll stop receiving alerts."
        ),
        "unsubscribe_yes": "Yes, unsubscribe",
        "unsubscribe_no": "Cancel",
        "unsubscribed": (
            "✅ You've been unsubscribed. Come back anytime with /subscribe. "
            "Stay safe."
        ),
        "unsubscribe_cancelled": "👍 Your subscription is kept.",
        "not_subscribed": "ℹ️ You're not subscribed.",
        "status": (
            "ℹ️ <b>Your subscription</b>\n\n"
            "📍 Location: {location}\n"
            "🕒 Subscribed since: {since}\n"
            "🛰️ Last successful data check: {last_check}\n"
            "🗣️ Alert language: {language}"
        ),
        "status_location_gps": "pinned point ({lat:.3f}, {lon:.3f})",
        "last_check_never": "not yet",
        "language_prompt": "🗣️ Choose your alert language:",
        "language_set": "✅ Alert language set to <b>English</b>.",
        "alert_single": (
            "🔥 <b>Possible heat alert</b>\n\n"
            "📍 near <b>{place}</b> — <b>{distance} km</b> {direction}\n"
            "🕒 {detected} Algeria time\n\n"
            "⚠️ This is a satellite heat detection, <b>not a confirmed fire</b>, "
            "and may be delayed by an hour or more. Verify locally or call "
            "Civil Protection at <b>{civil}</b> before taking any action."
        ),
        "alert_multi_header": "🔥 <b>Alert: {count} heat hotspots near you</b> (Algeria time)\n",
        "alert_multi_item": "📍 near <b>{place}</b> — <b>{distance} km</b> {direction} — 🕒 {detected}",
        "alert_more": "➕ and <b>{count}</b> more heat hotspots nearby",
        "alert_multi_footer": (
            "\n⚠️ Satellite detection may be delayed or false. Verify locally "
            "and call Civil Protection at <b>{civil}</b>."
        ),
        "alert_map_button": "🛰️ View on satellite map",
        "channel_post": (
            "🔥 <b>New heat hotspot</b> — near <b>{place}</b>\n"
            "📍 Coordinates: {lat:.4f}, {lon:.4f}\n"
            "🕒 {detected} Algeria time · confidence: {confidence}\n\n"
            "⚠️ Unconfirmed satellite detection. Emergency: <b>{civil}</b>"
        ),
        "unknown_command": "ℹ️ I didn't understand that. Use /help.",
        "generic_error": "⚠️ A temporary error occurred. Please try again.",
        "btn_subscribe": "📍 Subscribe",
        "btn_help": "ℹ️ Help",
        "btn_language": "🗣️ العربية",
    },
}

# Compass directions localized per language, indexed the same way as
# geo_utils.bearing_to_compass returns (N, NE, E, SE, S, SW, W, NW).
DIRECTIONS = {
    "ar": {
        "N": "شمالًا", "NE": "شمال شرق", "E": "شرقًا", "SE": "جنوب شرق",
        "S": "جنوبًا", "SW": "جنوب غرب", "W": "غربًا", "NW": "شمال غرب",
    },
    "fr": {
        "N": "au nord", "NE": "au nord-est", "E": "à l'est", "SE": "au sud-est",
        "S": "au sud", "SW": "au sud-ouest", "W": "à l'ouest", "NW": "au nord-ouest",
    },
    "en": {
        "N": "north", "NE": "north-east", "E": "east", "SE": "south-east",
        "S": "south", "SW": "south-west", "W": "west", "NW": "north-west",
    },
}


def normalize_lang(lang: str | None) -> str:
    """Return a supported language code, falling back to the default."""
    if lang in SUPPORTED_LANGUAGES:
        return lang
    return DEFAULT_LANGUAGE


def t(lang: str | None, key: str, **kwargs) -> str:
    """
    Look up a localized string and fill in ``{placeholders}``.

    ``{civil}`` (the Civil Protection number) is always injected so callers
    never have to pass it explicitly.
    """
    lang = normalize_lang(lang)
    table = STRINGS.get(lang, STRINGS[DEFAULT_LANGUAGE])
    template = table.get(key)
    if template is None:
        # Fall back to Arabic, then to the raw key so nothing crashes.
        template = STRINGS[DEFAULT_LANGUAGE].get(key, key)
    kwargs.setdefault("civil", CIVIL_PROTECTION_NUMBER)
    try:
        return template.format(**kwargs)
    except (KeyError, IndexError):
        # A missing placeholder should never take the bot down.
        return template


def direction_label(lang: str | None, compass: str) -> str:
    """Translate a compass code (e.g. 'NE') into a localized phrase."""
    lang = normalize_lang(lang)
    return DIRECTIONS.get(lang, DIRECTIONS[DEFAULT_LANGUAGE]).get(compass, compass)
