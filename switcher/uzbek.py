"""Uzbek words, so that Switcher leaves them alone for people who also write Uzbek.

There is no Uzbek in the frequency data the models are built from, so "олдин" (before) looks
like a Russian typo of "один" and "улар" (they) like "удар".  This is a list of the most
common Uzbek words and suffixes in the Latin alphabet; the Cyrillic spellings are derived
from it, both proper ("йўқ", "раҳмат") and as typed on a Russian keyboard ("йук", "рахмат").

It only ever stops a change: a word that looks Uzbek is not autocorrected or switched.
"""

from __future__ import annotations

import re
from functools import lru_cache

# Base forms and frequent word forms (Latin, apostrophe as ').
_WORDS = """
men sen u biz siz ular bu shu o'sha ana mana kim nima qaysi qanday qancha nega nimaga nimalar qachon qayer
qayerda qayerga qayerdan hamma hammasi har hech hechkim hechnarsa narsa o'z o'zi o'zim o'zing o'zimiz o'zingiz
o'zlari menga senga unga bizga sizga ularga meni seni uni bizni sizni ularni mening sening uning bizning
sizning ularning menda senda unda bizda sizda ularda mendan sendan undan bizdan sizdan ulardan bunga buni
bunda bundan buning shunga shuni shunda shundan shuning shunday bunday unday shuncha buncha
va ham bilan uchun lekin ammo biroq yoki agar chunki balki faqat hatto endi yana hali allaqachon albatta
mayli xo'p ha yo'q emas bor kerak mumkin shart kabi singari haqida orqali bo'yicha tomon qadar gacha
keyin oldin avval so'ng so'ngra beri deb degan desa ekan emish edi ekanligi go'yo nahotki axir
bugun kecha ertaga indinga hozir hozirgi doim doimo ba'zan tez sekin erta kech kun kuni kunlar tun
ertalab kechqurun tushlik hafta oy yil soat daqiqa vaqt payt paytda zamon
salom assalomu alaykum rahmat xayr xush kelibsiz yaxshimisiz yaxshimisan qalaysiz qalay yaxshi yomon
zo'r ajoyib chiroyli go'zal kichik katta yangi eski ko'p oz kam juda eng ancha sal
bir ikki uch to'rt besh olti yetti sakkiz to'qqiz o'n yigirma o'ttiz qirq ellik oltmish yetmish sakson
to'qson yuz ming million birinchi ikkinchi uchinchi
iltimos kechirasiz uzr marhamat xo'sh qani tushunarli tushundim tushunmadim bilmayman bilaman bilasizmi
ko'rishguncha ko'rishamiz gaplashamiz qo'ng'iroq
odam odamlar bola bolalar ota ona aka uka opa singil oila do'st do'stim do'stlar uy ish maktab
universitet shahar qishloq yo'l mashina pul narx suv non ovqat choy telefon xabar savol javob gap so'z
til o'zbek o'zbekcha ruscha inglizcha tilida toshkent o'zbekiston dunyo hayot sog'liq bosh ko'z qo'l
yurak joy xona eshik deraza kitob dars o'qituvchi talaba vazifa loyiha kompaniya mijoz hujjat fayl rasm
xat manzil raqam sana bayram tug'ilgan muammo yechim fikr reja natija holat sabab maqsad imkoniyat
yordam ma'lumot ma'no dastur ilova tizim xizmat buyurtma to'lov hisob chegirma sovg'a tabrik
uzoq yaqin oson qiyin muhim kerakli to'g'ri noto'g'ri tayyor band bo'sh issiq sovuq shirin rost yolg'on
aniq ehtimol deyarli hamisha ko'proq kamroq yaxshiroq
bo'ladi bo'ldi bo'lsa bo'lgan bo'lib bo'lmaydi bo'lmadi bo'lishi bo'l bo'lsin edim eding edik edingiz
"""

# Verb roots: a finite form is a root plus the suffixes below ("kel" + "dim").
_ROOTS = """
bor kel ket qil ber ol ko'r de bil yoz o'qi ishla yur tur o'tir yot ye ich gapir ayt so'ra kut top qo'y
chiq kir qayt boshla tugat sev yoqtir xohla ista tushun yubor jo'nat sot to'la yasha o'yna uxla tayyorla
eshit qara bo'l yoq o'yla esla unut yordamlash gaplash ko'rish uchrash tanish o'rgan o'rgat tekshir
"""

_SUFFIXES = """
lar ni ning ga ka qa da ta dan tan dagi gacha im ing i si imiz ingiz miz ngiz m ng cha lik li siz chi mi
dir day
aman asan adi amiz asiz ishadi dim ding di dik dingiz dilar ishdi yapman yapsan yapti yapmiz yapsiz
moqda moq gan gani ganman ganda guncha ib ing ingiz sa sam sang sak sangiz ish may mayman maydi madim madi
masdan yman ydi ymiz ysiz ydilar ylik ay ayman aydi aysiz
"""

_LETTERS = {
    "a": "а", "b": "б", "d": "д", "e": "е", "f": "ф", "g": "г", "h": "ҳ", "i": "и", "j": "ж", "k": "к",
    "l": "л", "m": "м", "n": "н", "o": "о", "p": "п", "q": "қ", "r": "р", "s": "с", "t": "т", "u": "у",
    "v": "в", "x": "х", "y": "й", "z": "з", "'": "ъ",
}
_PAIRS = {"o'": "ў", "g'": "ғ", "sh": "ш", "ch": "ч", "yo": "ё", "yu": "ю", "ya": "я", "ye": "е"}
# What people type for the Uzbek-only letters on a Russian keyboard.
_RU_KEYBOARD = str.maketrans({"ў": "у", "қ": "к", "ғ": "г", "ҳ": "х"})
_APOSTROPHES = str.maketrans({"ʻ": "'", "ʼ": "'", "`": "'", "‘": "'", "’": "'"})
_UZBEK_ONLY = re.compile(r"[ўқғҳ]|[og]'")


def to_cyrillic(word: str) -> str:
    """Latin Uzbek to Cyrillic: "yo'q" → "йўқ", "rahmat" → "раҳмат", "ertaga" → "эртага"."""
    w = word.lower().translate(_APOSTROPHES)
    out, i = [], 0
    while i < len(w):
        pair = w[i:i + 2]
        if pair in _PAIRS and not (pair == "yo" and w[i + 2:i + 3] == "'"):  # "yo'q": й + ў, not ё
            out.append(_PAIRS[pair])
            i += 2
        elif w[i] == "e" and i == 0:
            out.append("э")
            i += 1
        else:
            out.append(_LETTERS.get(w[i], w[i]))
            i += 1
    return "".join(out)


def _forms(latin: str) -> set[str]:
    cyrillic = to_cyrillic(latin)
    return {latin, latin.replace("'", ""), cyrillic, cyrillic.translate(_RU_KEYBOARD)}


def _expand(text: str) -> frozenset[str]:
    return frozenset(form for word in text.split() for form in _forms(word) if form)


@lru_cache(maxsize=1)
def _tables() -> tuple[frozenset[str], frozenset[str], tuple[str, ...]]:
    words = _expand(_WORDS)
    stems = words | _expand(_ROOTS)
    suffixes = tuple(sorted(_expand(_SUFFIXES), key=len, reverse=True))
    return words, stems, suffixes


def _inflected(word: str, stems: frozenset[str], suffixes: tuple[str, ...], depth: int) -> bool:
    if word in stems:
        return True
    if depth == 0:
        return False
    return any(word.endswith(s) and len(word) - len(s) >= 2 and _inflected(word[:-len(s)], stems, suffixes, depth - 1)
               for s in suffixes)


@lru_cache(maxsize=4096)
def known(word: str) -> bool:
    """A listed Uzbek word or an inflected form of one: "олдин", "oldin", "йук", "odamlarga", "келдим"."""
    w = word.lower().translate(_APOSTROPHES)
    if not w:
        return False
    words, stems, suffixes = _tables()
    return w in words or _inflected(w, stems, suffixes, 3)


def looks_uzbek(word: str) -> bool:
    """``known``, or spelt with a letter only Uzbek has: "ўзим", "qo'shiq"."""
    return bool(_UZBEK_ONLY.search(word.lower().translate(_APOSTROPHES))) or known(word)
