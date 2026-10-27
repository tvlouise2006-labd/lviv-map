import re
import json
from typing import Optional, Tuple, Dict, Any
from pydantic import BaseModel, Field
from backend.models import ParsedMessage, SightingAction, TransportType, Stop
from backend.transit import transit_manager
from backend.config import settings

class LLMSightingResult(BaseModel):
    is_noise: bool = Field(description="True if message is spam, ad, chat, or joke, False if it is a transit inspector report")
    action: str = Field(default="standing", description="One of: in_vehicle, standing, exited, cleared")
    route: Optional[str] = Field(default=None, description="Route number e.g. '46', '2', '8', '1а', 'тр30'")
    transport_type: str = Field(default="unknown", description="One of: bus, tram, trolleybus, unknown")
    fleet_number: Optional[str] = Field(default=None, description="Vehicle fleet/board number if specified, e.g. '1240'")
    location: Optional[str] = Field(default=None, description="Stop name, street name, or landmark")
    direction: Optional[str] = Field(default=None, description="Direction or destination name, e.g. 'Сихів', 'центр'")
    count: int = Field(default=2, description="Number of inspectors")

# Common Lviv landmark aliases and known stops with their approximate coordinates or canonical stop names
KNOWN_ALIASES: Dict[str, Tuple[str, float, float]] = {
    "дбк": ("ДБК (вул. Луганська)", 49.8087, 24.0435),
    "іскра": ("ТЦ Іскра (Сихів)", 49.7915, 24.0620),
    "іскру": ("ТЦ Іскра (Сихів)", 49.7915, 24.0620),
    "санта барбара": ("Санта-Барбара (просп. Червоної Калини)", 49.7824, 24.0628),
    "санта барбари": ("Санта-Барбара (просп. Червоної Калини)", 49.7824, 24.0628),
    "головна пошта": ("Головна пошта (вул. Словацького)", 49.8385, 24.0202),
    "головній пошті": ("Головна пошта (вул. Словацького)", 49.8385, 24.0202),
    "головної пошти": ("Головна пошта (вул. Словацького)", 49.8385, 24.0202),
    "головну пошту": ("Головна пошта (вул. Словацького)", 49.8385, 24.0202),
    "готель львів": ("Готель Львів (пр. Чорновола)", 49.8465, 24.0267),
    "готелю львів": ("Готель Львів (пр. Чорновола)", 49.8465, 24.0267),
    "митна": ("площа Митна", 49.8398, 24.0371),
    "митній": ("площа Митна", 49.8398, 24.0371),
    "дорошенка": ("вулиця Дорошенка", 49.8392, 24.0232),
    "довженка": ("Центр Довженка (Сихів)", 49.7984, 24.0575),
    "труша": ("вулиця Труша (кінцева Т2)", 49.8213, 23.9875),
    "бузкова": ("вулиця Бузкова", 49.8130, 24.0450),
    "бузковій": ("вулиця Бузкова", 49.8130, 24.0450),
    "угорська": ("вулиця Угорська", 49.8090, 24.0475),
    "угорській": ("вулиця Угорська", 49.8090, 24.0475),
    "зубрівська": ("вулиця Зубрівська", 49.7950, 24.0530),
    "зубрівській": ("вулиця Зубрівська", 49.7950, 24.0530),
    "липова алея": ("Липова алея", 49.8185, 24.0485),
    "липовій алеї": ("Липова алея", 49.8185, 24.0485),
    "пирогівка": ("Пирогівка (вул. Пасічна)", 49.8250, 24.0760),
    "пирогівці": ("Пирогівка (вул. Пасічна)", 49.8250, 24.0760),
    "стрийська наукова": ("Перехрестя Стрийська - Наукова", 49.7965, 24.0155),
    "стрийська-наукова": ("Перехрестя Стрийська - Наукова", 49.7965, 24.0155),
    "стрийської наукової": ("Перехрестя Стрийська - Наукова", 49.7965, 24.0155),
    "клуб науковий": ("Клуб Науковий (вул. Наукова)", 49.8000, 23.9900),
    "наукова": ("вулиця Наукова", 49.8010, 23.9920),
    "науковій": ("вулиця Наукова", 49.8010, 23.9920),
    "пасічна": ("вулиця Пасічна", 49.8270, 24.0750),
    "пасічній": ("вулиця Пасічна", 49.8270, 24.0750),
    "скейт парк": ("Скейт-парк (вул. Богдана Хмельницького)", 49.8560, 24.0410),
    "скей парку": ("Скейт-парк (вул. Богдана Хмельницького)", 49.8560, 24.0410),
    "скейт парку": ("Скейт-парк (вул. Богдана Хмельницького)", 49.8560, 24.0410),
    "дитяча залізниця": ("Дитяча залізниця (Стрийський парк)", 49.8250, 24.0280),
    "вікторії гарденсу": ("ТРЦ Victoria Gardens", 49.8075, 23.9770),
    "вікторії гарденс": ("ТРЦ Victoria Gardens", 49.8075, 23.9770),
    "вікторія гарденс": ("ТРЦ Victoria Gardens", 49.8075, 23.9770),
    "вікторії": ("ТРЦ Victoria Gardens", 49.8075, 23.9770),
    "кінг кросс": ("ТРЦ King Cross Leopolis", 49.7740, 24.0127),
    "кінг крос": ("ТРЦ King Cross Leopolis", 49.7740, 24.0127),
    "ашан": ("Ашан (Сокільники/Чорновола)", 49.7740, 24.0127),
    "ашану": ("Ашан (Сокільники/Чорновола)", 49.7740, 24.0127),
    "коледж": ("Коледж (вул. Вашингтона)", 49.8230, 24.0670),
    "райадміністрація": ("Райадміністрація (Шевченківський р-н)", 49.8630, 24.0230),
    "райадміністрації": ("Райадміністрація (Шевченківський р-н)", 49.8630, 24.0230),
    "райдержадміністрації": ("Райадміністрація (Шевченківський р-н)", 49.8630, 24.0230),
    "тролейбусна": ("вулиця Тролейбусна", 49.8020, 24.0040),
    "тролейбусної": ("вулиця Тролейбусна", 49.8020, 24.0040),
    "податкова": ("ДПА / Податкова (вул. Стрийська)", 49.8140, 24.0220),
    "податкової": ("ДПА / Податкова (вул. Стрийська)", 49.8140, 24.0220),
    "автовокзал": ("Автовокзал (вул. Стрийська)", 49.7874, 24.0176),
    "автовокзалі": ("Автовокзал (вул. Стрийська)", 49.7874, 24.0176),
    "автовокзалу": ("Автовокзал (вул. Стрийська)", 49.7874, 24.0176),
    "вокзал": ("Головний залізничний вокзал", 49.8398, 23.9945),
    "вокзалу": ("Головний залізничний вокзал", 49.8398, 23.9945),
    "оперний": ("Оперний театр", 49.8440, 24.0262),
    "оперного": ("Оперний театр", 49.8440, 24.0262),
    "коновальця": ("вулиця Коновальця", 49.8280, 24.0030),
    "грінченка": ("вулиця Грінченка", 49.8730, 24.0410),
    "спартака": ("СТРЦ Spartak (вул. Мазепи)", 49.8670, 24.0250),
    "спартак": ("СТРЦ Spartak (вул. Мазепи)", 49.8670, 24.0250),
    "політех": ("Львівська Політехніка", 49.8358, 24.0144),
    "політеху": ("Львівська Політехніка", 49.8358, 24.0144),
    "політесі": ("Львівська Політехніка", 49.8358, 24.0144),
    "політехніці": ("Львівська Політехніка", 49.8358, 24.0144),
    "політехніка": ("Львівська Політехніка", 49.8358, 24.0144),
    "скриня": ("ТЦ Скриня (вул. Городоцька)", 49.8335, 23.9970),
    "скрині": ("ТЦ Скриня (вул. Городоцька)", 49.8335, 23.9970),
    "аквапарк": ("Аквапарк Пляж (вул. Кн. Ольги)", 49.8050, 24.0010),
    "аквапарку": ("Аквапарк Пляж (вул. Кн. Ольги)", 49.8050, 24.0010),
    "патона": ("вулиця Патона", 49.8230, 23.9650),
    "лорта": ("завод ЛОРТА", 49.8250, 23.9530),
    "військовий госпіталь": ("Військовий госпіталь (вул. Чехова)", 49.8395, 24.0490),
    "військовому госпіталі": ("Військовий госпіталь (вул. Чехова)", 49.8395, 24.0490),
    "коперника": ("вулиця Коперника", 49.8365, 24.0210),
    "автостанція 6": ("Автостанція №6 (Личаківська)", 49.8430, 24.0880),
    "автостанції номер 5": ("Автостанція №5 (вул. Луганська)", 49.8050, 24.0510),
    "личаківська": ("вулиця Личаківська", 49.8410, 24.0550),
    "личаківській": ("вулиця Личаківська", 49.8410, 24.0550),
    "чорновола": ("проспект Чорновола", 49.8520, 24.0250),
    "шухевича": ("вулиця Шухевича", 49.8370, 24.0350),
    "соборна": ("площа Соборна", 49.8385, 24.0345),
    "соборній": ("площа Соборна", 49.8385, 24.0345),
    "кропивницького": ("площа Кропивницького", 49.8360, 24.0060),
    "ольги та єлизавети": ("Церква Ольги і Єлизавети", 49.8368, 24.0052),
    "саксаганського": ("вулиця Саксаганського", 49.8378, 24.0325),
    "шота руставелі": ("вулиця Шота Руставелі", 49.8315, 24.0355),
    "площі лема": ("площа Лема (Сахарова)", 49.8290, 24.0220),
    "будинок меблів": ("Будинок меблів (вул. Любінська)", 49.8215, 23.9780),
    "окружній": ("вулиця Окружна", 49.8190, 23.9870),
    "окружна": ("вулиця Окружна", 49.8190, 23.9870),
    "городоцькій": ("вулиця Городоцька", 49.8340, 23.9920),
    "городоцька": ("вулиця Городоцька", 49.8340, 23.9920),
    "бті": ("БТІ (вул. Липинського)", 49.8595, 24.0370),
    "куровця": ("вулиця Куровця", 49.8560, 23.9390),
    "таджицький": ("вулиця Таджицька (Пасічна)", 49.8205, 24.0780),
    "таджицькій": ("вулиця Таджицька (Пасічна)", 49.8205, 24.0780),
    "аркаса": ("вулиця Аркаса", 49.8080, 24.0040),
    "люблінська-виговського": ("Любінська - Виговського", 49.8170, 23.9690),
    "любінська": ("вулиця Любінська", 49.8190, 23.9740),
    "любінської": ("вулиця Любінська", 49.8190, 23.9740),
    "шевченка": ("вулиця Шевченка", 49.8515, 23.9920),
    "сихова": ("Сихів", 49.7930, 24.0580),
    "сихів": ("Сихів", 49.7930, 24.0580),
    "центру": ("Центр", 49.8400, 24.0280),
    "центр": ("Центр", 49.8400, 24.0280),
}

# Number words to digits (almost always trams in Lviv slang)
NUMBER_WORDS: Dict[str, Tuple[str, TransportType]] = {
    "одиничку": ("1", TransportType.TRAM),
    "одиниці": ("1", TransportType.TRAM),
    "двойку": ("2", TransportType.TRAM),
    "двійку": ("2", TransportType.TRAM),
    "двійці": ("2", TransportType.TRAM),
    "трійку": ("3", TransportType.TRAM),
    "трійці": ("3", TransportType.TRAM),
    "четвірку": ("4", TransportType.TRAM),
    "четвірці": ("4", TransportType.TRAM),
    "п'ятірку": ("5", TransportType.BUS),
    "шістку": ("6", TransportType.TRAM),
    "шістці": ("6", TransportType.TRAM),
    "сімку": ("7", TransportType.TRAM),
    "сімці": ("7", TransportType.TRAM),
    "вісімку": ("8", TransportType.TRAM),
    "вісімці": ("8", TransportType.TRAM),
    "дев'ятку": ("9", TransportType.TRAM),
}

# Regex patterns for Ukrainian transit reports (including ІіЇїЄєҐґ)
UKR_CHARS = r"А-Яа-яІіЇїЄєҐґA-Za-z0-9’'\-"

# Strict filter for military/mobilization/TCC messages (Art. 114-1/114-2 Criminal Code of Ukraine)
# Absolutely zero military/TCC/draft reports are processed or displayed
RE_MILITARY_FILTER = re.compile(
    r"\b(тцк|повістк[а-яіїєґ]*|повестк[а-яіїєґ]*|воєнком[а-яіїєґ]*|военком[а-яіїєґ]*|військов[а-яіїєґ]*|оливк[а-яіїєґ]*|піксель|блокпост[а-яіїєґ]*|мобілізац[а-яіїєґ]*|бусифікац[а-яіїєґ]*)\b",
    re.IGNORECASE
)

RE_NOISE = re.compile(
    r"(куп(?:ував|ляв|ив)\s+алкоголь|підкажіть|хто\s+знає|скажіть\s+будь\s*ласка|реклама|знижк|підписуйтесь|шукаю|розіграш)",
    re.IGNORECASE
)

RE_CLEARED = re.compile(
    r"\b(чисто|вийшли\s+вже|вже\s+вийшли|нема|немає|проїхали|контролю\s+нема)\b",
    re.IGNORECASE
)

RE_EXITED = re.compile(
    r"(?<!не\s)\b(вийшл[аои]|виходять|висадили|зійшл[аои])\b",
    re.IGNORECASE
)

RE_ENTERED = re.compile(
    r"\b(зайшл[аои]|сіл[аои]|сидять|їде|їдуть|виїхали|пересіл[аои]|залізл[аои]|заскочил[аои]|будуть\s+на\s+\d+\s+сідати)\b",
    re.IGNORECASE
)

RE_DIRECTION = re.compile(
    rf"(?:в\s+сторону|сторону|в\s+сторну|в\s+строну|в\s+сторок|в\s+бік|у\s+бік|в\s+напрямку|напрямок|до\s+площі|від\s+[{UKR_CHARS}]+\s+на)\s+([{UKR_CHARS}\s\.\(\)]+?)(?:$|[,;!\n]|\bі\s+не\b|\bхз\b)",
    re.IGNORECASE
)

RE_LOCATION = re.compile(
    rf"(?:на\s+зупинці|зупинка|біля\s+зупинки|перехрестя|біля|коло|від|на)\s+[«\"\']?([{UKR_CHARS}\s\.]+)[»\"\']?",
    re.IGNORECASE
)

class MessageParser:
    def parse(self, text: str, use_llm: bool = True) -> ParsedMessage:
        # Immediate rejection of any military/mobilization/TCC content
        if RE_MILITARY_FILTER.search(text):
            return ParsedMessage(action=SightingAction.NOISE, confidence=0.0)

        rule_res = self._parse_with_rules(text)
        if rule_res.action == SightingAction.NOISE or rule_res.confidence >= 0.75:
            return rule_res

        if use_llm and settings.GEMINI_API_KEY:
            try:
                llm_res = self._parse_with_gemini(text)
                if llm_res:
                    return llm_res
            except Exception as e:
                print(f"[Parser] Gemini extraction failed, falling back to rules: {e}")
        return rule_res

    def _parse_with_gemini(self, text: str) -> Optional[ParsedMessage]:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=settings.GEMINI_API_KEY)
        prompt = (
            "You are a public transit inspector sighting extractor for Lviv, Ukraine. "
            "Extract transit inspector sightings from this Telegram message.\n"
            f"Report text: {text}"
        )
        response = client.models.generate_content(
            model="gemini-3.8-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_json_schema=LLMSightingResult.model_json_schema(),
            )
        )
        if not response.text:
            return None
        data = LLMSightingResult.model_validate_json(response.text)
        if data.is_noise:
            return ParsedMessage(action=SightingAction.NOISE, confidence=0.0)

        act = SightingAction.STANDING
        if data.action == "in_vehicle":
            act = SightingAction.IN_VEHICLE
        elif data.action == "exited":
            act = SightingAction.EXITED
        elif data.action == "cleared":
            act = SightingAction.CLEARED

        tt = TransportType.UNKNOWN
        if data.transport_type == "tram":
            tt = TransportType.TRAM
        elif data.transport_type == "trolleybus":
            tt = TransportType.TROLLEYBUS
        elif data.transport_type == "bus":
            tt = TransportType.BUS

        route_obj = None
        route_raw = data.route
        if route_raw:
            route_obj = transit_manager.find_route(route_raw, tt)
            if route_obj:
                tt = route_obj.transport_type

        matched_stop_id = None
        matched_stop_name = None
        stop_lat = None
        stop_lon = None
        location_text = data.location

        if location_text:
            loc_lower = location_text.lower()
            for alias, (alias_name, a_lat, a_lon) in KNOWN_ALIASES.items():
                if alias in loc_lower:
                    location_text = alias_name
                    stop_lat = a_lat
                    stop_lon = a_lon
                    matched_stop_name = alias_name
                    break
            if not stop_lat:
                gs = transit_manager.fuzzy_match_stop(location_text, score_cutoff=60)
                if gs:
                    matched_stop_id = gs.id
                    matched_stop_name = gs.name
                    stop_lat = gs.lat
                    stop_lon = gs.lon

        # Fallback to rules for coordinates if LLM location string wasn't in GTFS stops
        if not stop_lat:
            rule_fallback = self._parse_with_rules(text)
            if rule_fallback.stop_lat:
                stop_lat = rule_fallback.stop_lat
                stop_lon = rule_fallback.stop_lon
                matched_stop_id = rule_fallback.matched_stop_id
                matched_stop_name = rule_fallback.matched_stop_name
                location_text = location_text or rule_fallback.location_text

        if not stop_lat:
            stop_lat = 49.8419
            stop_lon = 24.0315

        conf = 0.95 if (route_raw and matched_stop_name) else 0.85

        return ParsedMessage(
            action=act,
            route_raw=route_raw,
            route_id=route_obj.id if route_obj else None,
            transport_type=tt,
            fleet_number=data.fleet_number,
            location_text=location_text,
            matched_stop_id=matched_stop_id,
            matched_stop_name=matched_stop_name,
            stop_lat=stop_lat,
            stop_lon=stop_lon,
            direction_hint=data.direction,
            confidence=conf,
            count=data.count or 2
        )

    def _parse_with_rules(self, text: str) -> ParsedMessage:
        clean = text.strip()
        clean_lower = clean.lower()

        # 1. Filter obvious noise or military content
        if RE_MILITARY_FILTER.search(clean) or RE_NOISE.search(clean):
            return ParsedMessage(action=SightingAction.NOISE, confidence=0.0)

        # 2. Check action
        action = SightingAction.STANDING
        if RE_CLEARED.search(clean):
            action = SightingAction.CLEARED
        elif RE_EXITED.search(clean):
            action = SightingAction.EXITED
        elif RE_ENTERED.search(clean):
            action = SightingAction.IN_VEHICLE

        # 3. Extract count of inspectors
        count = 2
        m_cnt = re.search(r"\b([1-5])\s*(?:контролер|контролю|чоловік|людин|контр)", clean_lower)
        if m_cnt:
            count = int(m_cnt.group(1))
        elif re.search(r"\b(одна|один)\b", clean_lower):
            count = 1
        elif re.search(r"\b(троє|три)\b", clean_lower):
            count = 3
        elif re.search(r"\b(четверо|чотири)\b", clean_lower):
            count = 4

        # 4. Extract fleet number if present, e.g. "(1240 борт)" or "борт 1240"
        fleet_number = None
        m_fleet = re.search(r"\b(\d{3,4})\s*борт|\bборт\s*(\d{3,4})\b", clean_lower)
        if m_fleet:
            fleet_number = m_fleet.group(1) or m_fleet.group(2)

        # 5. Extract transport type and route number
        route_raw = None
        transport_type = TransportType.UNKNOWN

        # Check explicit transport words (making sure NOT to match "трол" inside "контролер"!)
        if re.search(r"\b(трамва[їйяєюі]?|травма[їйяєюі]?|трам|тм\s*\d+)\b", clean_lower):
            transport_type = TransportType.TRAM
        elif re.search(r"\b(тролейбус[іауом]?|трол[іа]?|тр\s*\d+)\b", clean_lower):
            transport_type = TransportType.TROLLEYBUS
        elif re.search(r"\b(автобус[іауом]?|маршрутк[аіуою]|авт)\b", clean_lower):
            transport_type = TransportType.BUS

        # Check shorthand like "тм1", "тр30", "а46"
        m_short = re.search(r"\b(тм|тр|а)\s*(\d{1,2}[а-яіїєa-z]?)\b", clean_lower)
        if m_short:
            prefix, r_num = m_short.group(1), m_short.group(2)
            route_raw = r_num
            if prefix == "тм":
                transport_type = TransportType.TRAM
            elif prefix == "тр":
                transport_type = TransportType.TROLLEYBUS
            elif prefix == "а":
                transport_type = TransportType.BUS

        # Check number words like "зайшли в двойку"
        if not route_raw:
            for word, (digit, ttype) in NUMBER_WORDS.items():
                if re.search(rf"\b{word}\b", clean_lower):
                    route_raw = digit
                    if transport_type == TransportType.UNKNOWN:
                        transport_type = ttype
                    break

        # Find route number candidates via contextual regexes
        if not route_raw:
            route_patterns = [
                r"(?:зайшл[аои]\s+(?:в|у)|сіл[аои]\s+(?:в|у)|вийшл[аои]\s+(?:з|із)|сидять\s+(?:в|у)|були\s+(?:в|у)|їд(?:е|уть)\s+(?:в|у)|як\s+їде|контроль\s+(?:у|в)|будуть\s+на)\s+(\d{1,2}[а-яіїєa-z]?)\b",
                r"\b(\d{1,2}[а-яіїєa-z]?)\s*(?:трамва|травма|автобус|тролейбус|маршрутк)",
                r"\b(?:в|у)\s+(\d{1,2}[а-яіїєa-z]?)\b(?!\s*(?:контролер|чоловік|людин|борт|годин|ранку|хв))",
            ]
            for pat in route_patterns:
                m = re.search(pat, clean_lower)
                if m:
                    cand = m.group(1).strip()
                    # Avoid matching "автостанція 6" or "номер 5"
                    if re.search(rf"(?:автостанці[яї]|номер)\s+{re.escape(cand)}\b", clean_lower):
                        continue
                    route_raw = cand
                    if action == SightingAction.STANDING and re.search(rf"\b(?:в|у)\s+{re.escape(cand)}\b", clean_lower):
                        action = SightingAction.IN_VEHICLE
                    break

        # Match route against GTFS routes
        route_obj = None
        if route_raw:
            # In Lviv, routes 1..9 without letter when no bus is mentioned are usually trams (except 3a, 1a, 5a, 6a, 8a, 9 as bus if specified)
            if transport_type == TransportType.UNKNOWN and route_raw in {"1", "2", "3", "4", "6", "7", "8"}:
                route_obj = transit_manager.find_route(route_raw, TransportType.TRAM)
            elif transport_type == TransportType.UNKNOWN and route_raw in {"22", "23", "24", "25", "27", "30", "31", "32", "33", "38"}:
                route_obj = transit_manager.find_route(route_raw, TransportType.TROLLEYBUS)
            if not route_obj:
                route_obj = transit_manager.find_route(route_raw, transport_type)
            if route_obj:
                transport_type = route_obj.transport_type

        # 6. Extract direction hint first so we can separate location from direction
        direction_hint = None
        direction_span = None
        m_dir = RE_DIRECTION.search(clean)
        if m_dir:
            dir_text = m_dir.group(1).strip()
            dir_text = re.sub(r'[\.!?,]+$', '', dir_text).strip()
            if 2 < len(dir_text) < 45:
                direction_hint = dir_text
                direction_span = m_dir.span()

        # Also check "з X на Y" or "від X на Y"
        if not direction_hint:
            m_from_to = re.search(rf"(?:від|з)\s+([{UKR_CHARS}\s]+?)\s+(?:на|еа|до)\s+([{UKR_CHARS}\s]+?)(?:$|[,;!\n])", clean)
            if m_from_to:
                direction_hint = m_from_to.group(2).strip()
                direction_span = m_from_to.span(2)

        # Text without the direction clause for primary stop matching
        primary_text_lower = clean_lower
        if direction_span:
            primary_text_lower = (clean_lower[:direction_span[0]] + " " + clean_lower[direction_span[1]:]).strip()

        # 7. Extract location text and match stop
        location_text = None
        matched_stop_id = None
        matched_stop_name = None
        stop_lat = None
        stop_lon = None

        # Sort aliases by length descending so more specific aliases ("стрийська наукова", "клуб науковий") match before "наукова"
        sorted_aliases = sorted(KNOWN_ALIASES.items(), key=lambda x: len(x[0]), reverse=True)

        # First search aliases in primary text (outside direction clause)
        for alias, (alias_name, a_lat, a_lon) in sorted_aliases:
            if alias in ("сихова", "сихів", "центру", "центр"):
                continue  # Don't match generic areas unless in fallback
            if re.search(rf"(?<![а-яіїєґa-z]){re.escape(alias)}(?![а-яіїєґa-z])", primary_text_lower):
                location_text = alias_name
                stop_lat = a_lat
                stop_lon = a_lon
                matched_stop_name = alias_name
                gs = transit_manager.fuzzy_match_stop(alias, score_cutoff=65)
                if gs:
                    matched_stop_id = gs.id
                break

        # If not in aliases, extract from prepositions in primary text and fuzzy match GTFS
        if not stop_lat:
            for m_loc in RE_LOCATION.finditer(primary_text_lower):
                raw_loc = m_loc.group(1).strip()
                raw_loc = re.split(r'\b(в\s+сторону|в\s+бік|у\s+бік|в\s+напрямку|як\s+їде|сіли|стоять|стоїть|зайшли|вийшли|контролер[иів]?|двоє|троє)\b', raw_loc)[0].strip()
                raw_loc = re.sub(r'[\.!?,«»"\']+$', '', raw_loc).strip()
                if len(raw_loc) >= 3 and not raw_loc.isdigit():
                    gs = transit_manager.fuzzy_match_stop(raw_loc, score_cutoff=65)
                    if gs:
                        location_text = raw_loc
                        matched_stop_id = gs.id
                        matched_stop_name = gs.name
                        stop_lat = gs.lat
                        stop_lon = gs.lon
                        break

        # If still not found (e.g. no preposition like "2 контролери хімічна в 9 сторону форуму"), scan words in primary text
        if not stop_lat:
            stop_ignore_words = {
                "контролери", "контролера", "контролер", "контроль", "зайшли", "вийшли",
                "сіли", "сидять", "їдуть", "стоять", "стоїть", "трамвай", "трамваї",
                "тролейбус", "тролейбусі", "автобус", "автобусі", "маршрутка", "маршрутці",
                "сторону", "напрямку", "поліцією", "поліція", "чоловік", "людини", "двоє", "троє"
            }
            tokens = re.findall(rf"[{UKR_CHARS}]{{4,}}", primary_text_lower)
            for tok in tokens:
                if tok in stop_ignore_words or tok.isdigit():
                    continue
                gs = transit_manager.fuzzy_match_stop(tok, score_cutoff=80)
                if gs:
                    location_text = tok
                    matched_stop_id = gs.id
                    matched_stop_name = gs.name
                    stop_lat = gs.lat
                    stop_lon = gs.lon
                    break

        # Fallback: check aliases in the entire text (including direction hint if no origin stop was specified, e.g. "Сіли в 2 трамвай в сторону центру")
        if not stop_lat:
            for alias, (alias_name, a_lat, a_lon) in sorted_aliases:
                if re.search(rf"(?<![а-яіїєґa-z]){re.escape(alias)}(?![а-яіїєґa-z])", clean_lower):
                    location_text = f"Напрямок: {alias_name}" if direction_hint else alias_name
                    stop_lat = a_lat
                    stop_lon = a_lon
                    matched_stop_name = alias_name
                    break

        # Default fallback to central Lviv if location is completely absent
        if not stop_lat:
            stop_lat = 49.8419
            stop_lon = 24.0315
            confidence = 0.45 if route_raw else 0.30
        else:
            if route_raw and matched_stop_name:
                confidence = 0.92
            elif matched_stop_name:
                confidence = 0.78
            else:
                confidence = 0.65

        return ParsedMessage(
            action=action,
            route_raw=route_raw,
            route_id=route_obj.id if route_obj else None,
            transport_type=transport_type,
            fleet_number=fleet_number,
            location_text=location_text,
            matched_stop_id=matched_stop_id,
            matched_stop_name=matched_stop_name,
            stop_lat=stop_lat,
            stop_lon=stop_lon,
            direction_hint=direction_hint,
            confidence=confidence,
            count=count
        )

parser = MessageParser()
