import math
import re
import json
from typing import Optional, Tuple, Dict, Any, List
from pydantic import BaseModel, Field
from backend.models import ParsedMessage, SightingAction, TransportType, Stop, Route
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


# Common Lviv landmark aliases, slang names, and known stops with their approximate coordinates
KNOWN_ALIASES: Dict[str, Tuple[str, float, float]] = {
    "дбк": ("ДБК (вул. Луганська)", 49.8087, 24.0435),
    "іскра": ("ТЦ Іскра (Сихів)", 49.7915, 24.0620),
    "іскру": ("ТЦ Іскра (Сихів)", 49.7915, 24.0620),
    "іскри": ("ТЦ Іскра (Сихів)", 49.7915, 24.0620),
    "іскрі": ("ТЦ Іскра (Сихів)", 49.7915, 24.0620),
    "санта барбара": ("Санта-Барбара (просп. Червоної Калини)", 49.7824, 24.0628),
    "санта барбари": ("Санта-Барбара (просп. Червоної Калини)", 49.7824, 24.0628),
    "санта-барбара": ("Санта-Барбара (просп. Червоної Калини)", 49.7824, 24.0628),
    "санта": ("Санта-Барбара (просп. Червоної Калини)", 49.7824, 24.0628),
    "санти": ("Санта-Барбара (просп. Червоної Калини)", 49.7824, 24.0628),
    "санту": ("Санта-Барбара (просп. Червоної Калини)", 49.7824, 24.0628),
    "санті": ("Санта-Барбара (просп. Червоної Калини)", 49.7824, 24.0628),
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
    "стрийському перехресті": ("Перехрестя Стрийська - Наукова", 49.7965, 24.0155),
    "стрийське перехрестя": ("Перехрестя Стрийська - Наукова", 49.7965, 24.0155),
    "стрийського перехрестя": ("Перехрестя Стрийська - Наукова", 49.7965, 24.0155),
    "церкві анни": ("Церква святої Анни (вул. Городоцька)", 49.8415, 24.0153),
    "церкві анній": ("Церква святої Анни (вул. Городоцька)", 49.8415, 24.0153),
    "церква анни": ("Церква святої Анни (вул. Городоцька)", 49.8415, 24.0153),
    "святої анни": ("Церква святої Анни (вул. Городоцька)", 49.8415, 24.0153),
    "ярошенській": ("вулиця Ярошенка", 49.8183, 24.0341),
    "ярошенка": ("вулиця Ярошенка", 49.8183, 24.0341),
    "форум": ("ТРЦ Forum Lviv", 49.8498, 24.0225),
    "форуму": ("ТРЦ Forum Lviv", 49.8498, 24.0225),
    "форумі": ("ТРЦ Forum Lviv", 49.8498, 24.0225),
    "магнус": ("ТЦ Магнус (вул. Шпитальна)", 49.8446, 24.0222),
    "магнусу": ("ТЦ Магнус (вул. Шпитальна)", 49.8446, 24.0222),
    "магнусі": ("ТЦ Магнус (вул. Шпитальна)", 49.8446, 24.0222),
    "площа ринок": ("площа Ринок", 49.8419, 24.0315),
    "площі ринок": ("площа Ринок", 49.8419, 24.0315),
    "шувар": ("Ринок Шувар (Сихів)", 49.7988, 24.0472),
    "шуварі": ("Ринок Шувар (Сихів)", 49.7988, 24.0472),
    "шувару": ("Ринок Шувар (Сихів)", 49.7988, 24.0472),
    "південний": ("ТВК Південний (вул. Виговського)", 49.8122, 23.9745),
    "південного": ("ТВК Південний (вул. Виговського)", 49.8122, 23.9745),
    "південному": ("ТВК Південний (вул. Виговського)", 49.8122, 23.9745),
    "сільмаш": ("Сільмаш (вул. Городоцька)", 49.8322, 23.9825),
    "сільмаші": ("Сільмаш (вул. Городоцька)", 49.8322, 23.9825),
    "провесінь": ("Провесінь (вул. Княгині Ольги)", 49.8005, 24.0005),
    "сахарова": ("вулиця Академіка Сахарова", 49.8260, 24.0185),
    "балатон": ("Балатон (вул. Мазепи)", 49.8650, 24.0310),
    "балатоні": ("Балатон (вул. Мазепи)", 49.8650, 24.0310),
    "галицьке перехрестя": ("Галицьке перехрестя", 49.8780, 24.0530),
    "галицькому перехресті": ("Галицьке перехрестя", 49.8780, 24.0530),
    "привокзальна": ("Привокзальний ринок", 49.8365, 24.0010),
    "привокзальній": ("Привокзальний ринок", 49.8365, 24.0010),
    "привокзального": ("Привокзальний ринок", 49.8365, 24.0010),
    "привокзальний": ("Привокзальний ринок", 49.8365, 24.0010),
    "сихівська": ("вулиця Сихівська", 49.7965, 24.0605),
    "сихівській": ("вулиця Сихівська", 49.7965, 24.0605),
    "сихівської": ("вулиця Сихівська", 49.7965, 24.0605),
    "погулянка": ("Погулянка", 49.8255, 24.0615),
    "погулянки": ("Погулянка", 49.8255, 24.0615),
    "погулянці": ("Погулянка", 49.8255, 24.0615),
    "клуб науковий": ("Клуб Науковий (вул. Наукова)", 49.8000, 23.9900),
    "наукова": ("вулиця Наукова", 49.8010, 23.9920),
    "науковій": ("вулиця Наукова", 49.8010, 23.9920),
    "наукової": ("вулиця Наукова", 49.8010, 23.9920),
    "пасічна": ("вулиця Пасічна", 49.8270, 24.0750),
    "пасічній": ("вулиця Пасічна", 49.8270, 24.0750),
    "пасічної": ("вулиця Пасічна", 49.8270, 24.0750),
    "скейт парк": ("Скейт-парк (вул. Богдана Хмельницького)", 49.8560, 24.0410),
    "скей парку": ("Скейт-парк (вул. Богдана Хмельницького)", 49.8560, 24.0410),
    "скейт парку": ("Скейт-парк (вул. Богдана Хмельницького)", 49.8560, 24.0410),
    "дитяча залізниця": ("Дитяча залізниця (Стрийський парк)", 49.8250, 24.0280),
    "вікторії гарденсу": ("ТРЦ Victoria Gardens", 49.8075, 23.9770),
    "вікторії гарденс": ("ТРЦ Victoria Gardens", 49.8075, 23.9770),
    "вікторія гарденс": ("ТРЦ Victoria Gardens", 49.8075, 23.9770),
    "вікторії": ("ТРЦ Victoria Gardens", 49.8075, 23.9770),
    "гарденс": ("ТРЦ Victoria Gardens", 49.8075, 23.9770),
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
    "тролейбусній": ("вулиця Тролейбусна", 49.8020, 24.0040),
    "податкова": ("ДПА / Податкова (вул. Стрийська)", 49.8140, 24.0220),
    "податкової": ("ДПА / Податкова (вул. Стрийська)", 49.8140, 24.0220),
    "податковій": ("ДПА / Податкова (вул. Стрийська)", 49.8140, 24.0220),
    "автовокзал": ("Автовокзал (вул. Стрийська)", 49.7874, 24.0176),
    "автовокзалі": ("Автовокзал (вул. Стрийська)", 49.7874, 24.0176),
    "автовокзалу": ("Автовокзал (вул. Стрийська)", 49.7874, 24.0176),
    "вокзал": ("Головний залізничний вокзал", 49.8398, 23.9945),
    "вокзалу": ("Головний залізничний вокзал", 49.8398, 23.9945),
    "вокзалі": ("Головний залізничний вокзал", 49.8398, 23.9945),
    "двірцева": ("Головний залізничний вокзал (пл. Двірцева)", 49.8398, 23.9945),
    "двірцевій": ("Головний залізничний вокзал (пл. Двірцева)", 49.8398, 23.9945),
    "оперний": ("Оперний театр", 49.8440, 24.0262),
    "оперного": ("Оперний театр", 49.8440, 24.0262),
    "оперному": ("Оперний театр", 49.8440, 24.0262),
    "коновальця": ("вулиця Коновальця", 49.8280, 24.0030),
    "грінченка": ("вулиця Грінченка", 49.8730, 24.0410),
    "спартака": ("СТРЦ Spartak (вул. Мазепи)", 49.8670, 24.0250),
    "спартак": ("СТРЦ Spartak (вул. Мазепи)", 49.8670, 24.0250),
    "спартаку": ("СТРЦ Spartak (вул. Мазепи)", 49.8670, 24.0250),
    "політех": ("Львівська Політехніка", 49.8358, 24.0144),
    "політеху": ("Львівська Політехніка", 49.8358, 24.0144),
    "політесі": ("Львівська Політехніка", 49.8358, 24.0144),
    "політехніці": ("Львівська Політехніка", 49.8358, 24.0144),
    "політехніка": ("Львівська Політехніка", 49.8358, 24.0144),
    "скриня": ("ТЦ Скриня (вул. Городоцька)", 49.8335, 23.9970),
    "скрині": ("ТЦ Скриня (вул. Городоцька)", 49.8335, 23.9970),
    "скриню": ("ТЦ Скриня (вул. Городоцька)", 49.8335, 23.9970),
    "аквапарк": ("Аквапарк Пляж (вул. Кн. Ольги)", 49.8050, 24.0010),
    "аквапарку": ("Аквапарк Пляж (вул. Кн. Ольги)", 49.8050, 24.0010),
    "патона": ("вулиця Патона", 49.8230, 23.9650),
    "лорта": ("завод ЛОРТА", 49.8250, 23.9530),
    "військовий госпіталь": ("Військовий госпіталь (вул. Чехова)", 49.8395, 24.0490),
    "військовому госпіталі": ("Військовий госпіталь (вул. Чехова)", 49.8395, 24.0490),
    "коперника": ("вулиця Коперника", 49.8365, 24.0210),
    "автостанція 6": ("Автостанція №6 (Личаківська)", 49.8430, 24.0880),
    "автостанції 6": ("Автостанція №6 (Личаківська)", 49.8430, 24.0880),
    "ас6": ("Автостанція №6 (Личаківська)", 49.8430, 24.0880),
    "ас-6": ("Автостанція №6 (Личаківська)", 49.8430, 24.0880),
    "автостанція 5": ("Автостанція №5 (вул. Луганська)", 49.8050, 24.0510),
    "автостанції 5": ("Автостанція №5 (вул. Луганська)", 49.8050, 24.0510),
    "автостанції номер 5": ("Автостанція №5 (вул. Луганська)", 49.8050, 24.0510),
    "ас5": ("Автостанція №5 (вул. Луганська)", 49.8050, 24.0510),
    "ас-5": ("Автостанція №5 (вул. Луганська)", 49.8050, 24.0510),
    "автостанція 2": ("Автостанція №2 (вул. Б. Хмельницького)", 49.8648, 24.0458),
    "автостанції 2": ("Автостанція №2 (вул. Б. Хмельницького)", 49.8648, 24.0458),
    "ас2": ("Автостанція №2 (вул. Б. Хмельницького)", 49.8648, 24.0458),
    "ас-2": ("Автостанція №2 (вул. Б. Хмельницького)", 49.8648, 24.0458),
    "ас 2": ("Автостанція №2 (вул. Б. Хмельницького)", 49.8648, 24.0458),
    "автостанція 3": ("Автостанція №3 (вул. Петлюри)", 49.8255, 23.9785),
    "автостанції 3": ("Автостанція №3 (вул. Петлюри)", 49.8255, 23.9785),
    "ас3": ("Автостанція №3 (вул. Петлюри)", 49.8255, 23.9785),
    "ас-3": ("Автостанція №3 (вул. Петлюри)", 49.8255, 23.9785),
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
    "любінській": ("вулиця Любінська", 49.8190, 23.9740),
    "новий цум": ("Новий ЦУМ (вул. Княгині Ольги)", 49.8082, 24.0012),
    "нового цуму": ("Новий ЦУМ (вул. Княгині Ольги)", 49.8082, 24.0012),
    "цум": ("Новий ЦУМ (вул. Княгині Ольги)", 49.8082, 24.0012),
    "цуму": ("Новий ЦУМ (вул. Княгині Ольги)", 49.8082, 24.0012),
    "цумі": ("Новий ЦУМ (вул. Княгині Ольги)", 49.8082, 24.0012),
    "княгині ольги": ("вулиця Княгині Ольги", 49.8115, 24.0030),
    "кн. ольги": ("вулиця Княгині Ольги", 49.8115, 24.0030),
    "володимира великого": ("вулиця Володимира Великого", 49.8075, 23.9950),
    "в. великого": ("вулиця Володимира Великого", 49.8075, 23.9950),
    "великого": ("вулиця Володимира Великого", 49.8075, 23.9950),
    "кульпарківська": ("вулиця Кульпарківська", 49.8140, 23.9870),
    "кульпарківській": ("вулиця Кульпарківська", 49.8140, 23.9870),
    "кульпарківської": ("вулиця Кульпарківська", 49.8140, 23.9870),
    "виговського": ("вулиця Виговського", 49.8180, 23.9710),
    "зелена": ("вулиця Зелена", 49.8215, 24.0510),
    "зеленій": ("вулиця Зелена", 49.8215, 24.0510),
    "зеленої": ("вулиця Зелена", 49.8215, 24.0510),
    "вашингтона": ("вулиця Джорджа Вашингтона", 49.8225, 24.0695),
    "шафарика": ("вулиця Шафарика (Пасічна)", 49.8215, 24.0805),
    "майорівка": ("Майорівка (вул. Пасічна)", 49.8180, 24.0825),
    "майорівці": ("Майорівка (вул. Пасічна)", 49.8180, 24.0825),
    "водоканал": ("Львівводоканал (вул. Зелена)", 49.8312, 24.0458),
    "водоканалу": ("Львівводоканал (вул. Зелена)", 49.8312, 24.0458),
    "водоканалі": ("Львівводоканал (вул. Зелена)", 49.8312, 24.0458),
    "тубдиспансер": ("Тубдиспансер (вул. Зелена)", 49.8070, 24.0660),
    "тубдиспансеру": ("Тубдиспансер (вул. Зелена)", 49.8070, 24.0660),
    "медінститут": ("Медуніверситет (вул. Пекарська / Личаківська)", 49.8375, 24.0510),
    "медінституту": ("Медуніверситет (вул. Пекарська / Личаківська)", 49.8375, 24.0510),
    "медунівер": ("Медуніверситет (вул. Пекарська / Личаківська)", 49.8375, 24.0510),
    "медуніверситет": ("Медуніверситет (вул. Пекарська / Личаківська)", 49.8375, 24.0510),
    "університет": ("ЛНУ ім. І. Франка (вул. Університетська)", 49.8404, 24.0223),
    "університету": ("ЛНУ ім. І. Франка (вул. Університетська)", 49.8404, 24.0223),
    "універ": ("ЛНУ ім. І. Франка (вул. Університетська)", 49.8404, 24.0223),
    "універу": ("ЛНУ ім. І. Франка (вул. Університетська)", 49.8404, 24.0223),
    "лісотех": ("Лісотехнічний університет (вул. Чупринки)", 49.8260, 24.0095),
    "лісотеху": ("Лісотехнічний університет (вул. Чупринки)", 49.8260, 24.0095),
    "чупринки": ("вулиця Генерала Чупринки", 49.8295, 24.0110),
    "бандери": ("вулиця Степана Бандери", 49.8350, 24.0110),
    "степана бандери": ("вулиця Степана Бандери", 49.8350, 24.0110),
    "петрушевича": ("площа Петрушевича", 49.8328, 24.0345),
    "франка": ("вулиця Івана Франка", 49.8305, 24.0335),
    "стрийський ринок": ("Стрийський ринок (вул. І. Франка)", 49.8292, 24.0332),
    "стрийському ринку": ("Стрийський ринок (вул. І. Франка)", 49.8292, 24.0332),
    "підвальна": ("вулиця Підвальна", 49.8428, 24.0358),
    "підвальній": ("вулиця Підвальна", 49.8428, 24.0358),
    "руська": ("вулиця Руська", 49.8415, 24.0332),
    "руській": ("вулиця Руська", 49.8415, 24.0332),
    "краківський": ("Краківський ринок", 49.8462, 24.0212),
    "краківському": ("Краківський ринок", 49.8462, 24.0212),
    "краківського": ("Краківський ринок", 49.8462, 24.0212),
    "добробут": ("Ринок Добробут (Центр)", 49.8448, 24.0282),
    "добробуту": ("Ринок Добробут (Центр)", 49.8448, 24.0282),
    "винниківський": ("Винниківський ринок (вул. Личаківська)", 49.8398, 24.0450),
    "винниківському": ("Винниківський ринок (вул. Личаківська)", 49.8398, 24.0450),
    "мотозавод": ("Мотозавод (вул. Городоцька)", 49.8305, 23.9735),
    "мотозаводу": ("Мотозавод (вул. Городоцька)", 49.8305, 23.9735),
    "мотозаводі": ("Мотозавод (вул. Городоцька)", 49.8305, 23.9735),
    "левандівка": ("Левандівка", 49.8445, 23.9620),
    "левандівці": ("Левандівка", 49.8445, 23.9620),
    "левандівки": ("Левандівка", 49.8445, 23.9620),
    "рясне": ("Рясне", 49.8730, 23.9480),
    "рясному": ("Рясне", 49.8730, 23.9480),
    "варшавська": ("вулиця Варшавська", 49.8585, 24.0085),
    "варшавській": ("вулиця Варшавська", 49.8585, 24.0085),
    "мазепи": ("вулиця Гетьмана Мазепи", 49.8675, 24.0285),
    "хмельницького": ("вулиця Богдана Хмельницького", 49.8565, 24.0415),
    "замарстинівська": ("вулиця Замарстинівська", 49.8580, 24.0290),
    "замарстинівській": ("вулиця Замарстинівська", 49.8580, 24.0290),
    "промислова": ("вулиця Промислова", 49.8575, 24.0460),
    "промисловій": ("вулиця Промислова", 49.8575, 24.0460),
    "червоної калини": ("проспект Червоної Калини (Сихів)", 49.7955, 24.0570),
    "проспект свободи": ("проспект Свободи", 49.8418, 24.0275),
    "свободи": ("проспект Свободи", 49.8418, 24.0275),
    "шевченка": ("вулиця Шевченка", 49.8515, 23.9920),
    "сихова": ("Сихів", 49.7930, 24.0580),
    "сихові": ("Сихів", 49.7930, 24.0580),
    "сихів": ("Сихів", 49.7930, 24.0580),
    "центру": ("Центр", 49.8400, 24.0280),
    "центрі": ("Центр", 49.8400, 24.0280),
    "центр": ("Центр", 49.8400, 24.0280),
}

# Context-aware multi-branch chains in Lviv: McDonald's ("мак"), Arsen ("арсен"), Epicentr ("епіцентр")
# Each entry: (display_name, lat, lon, context_keywords)
LVIV_MCDONALDS: List[Tuple[str, float, float, List[str]]] = [
    (
        "МакДональдз (просп. Червоної Калини, 60 — Сихів)",
        49.7946, 24.0573,
        ["сихів", "сихові", "сихова", "червоної калини", "калини", "довженка", "санта", "шувар", "іскра", "зубрівськ", "сихівськ"]
    ),
    (
        "МакДональдз (вул. Володимира Великого, 24а — Новий ЦУМ)",
        49.8078, 24.0018,
        ["володимира великого", "великого", "княгині ольги", "ольги", "цум", "аквапарк", "науков", "тролейбусн", "виговського", "аркаса"]
    ),
    (
        "МакДональдз (просп. Чорновола, 93 — Арсен)",
        49.8615, 24.0188,
        ["чорновола", "арсен", "липинського", "мазепи", "спартак", "грінченка", "замарстинівськ", "варшавськ", "райадміністрац", "бті"]
    ),
    (
        "МакДональдз (вул. Зелена, 147а — Сихівська)",
        49.8115, 24.0568,
        ["зелен", "дбк", "луганськ", "пасічн", "бузков", "угорськ", "фрезерн", "пирогівк", "вашингтона", "таджицьк", "ас5", "автостанції 5"]
    ),
    (
        "МакДональдз (ТРЦ Victoria Gardens — Кульпарківська)",
        49.8075, 23.9778,
        ["вікторі", "гарденс", "кульпарківськ", "щирецьк", "південн", "любінськ", "окружн"]
    ),
    (
        "МакДональдз (вул. Шевченка, 311б — Рясне)",
        49.8685, 23.9635,
        ["рясне", "рясному", "левандівк", "ряснянськ", "куровця", "шевченка"]
    ),
    (
        "МакДональдз (просп. Чорновола, 12 — Центр/Форум)",
        49.8468, 24.0256,
        ["готель львів", "форум", "куліша", "зернов", "краківськ"]
    ),
    (
        "МакДональдз (просп. Шевченка, 7 — Центр)",
        49.8390, 24.0308,
        ["проспект шевченка", "проспекті шевченка", "герцена", "саксаганського", "соборн", "галицьк", "митна", "франка", "петрушевича", "дорошенка", "коперника"]
    ),
    (
        "МакДональдз (King Cross Leopolis — Стрийська)",
        49.7745, 24.0125,
        ["кінг крос", "ашан", "автовокзал", "сокільник", "іподром", "стрийськ"]
    ),
    (
        "МакДональдз (просп. Свободи, 35 — Оперний)",
        49.8432, 24.0263,
        ["свободи", "оперн", "торгов", "добробут", "городоцьк", "магнус", "центр"]
    ),
]

LVIV_ARSEN: List[Tuple[str, float, float, List[str]]] = [
    ("ТЦ Арсен (просп. Чорновола, 93)", 49.8612, 24.0192, ["чорновола", "мазепи", "спартак", "грінченка", "липинського"]),
    ("ТЦ Арсен (вул. Зелена, 147)", 49.8118, 24.0562, ["зелен", "сихів", "дбк", "пасічн", "луганськ"]),
    ("ТЦ Арсен (вул. Патона, 37)", 49.8235, 23.9635, ["патона", "виговського", "любінськ", "лорта", "ряшівськ"]),
    ("ТЦ Арсен (вул. Наукова, 35а)", 49.8012, 23.9965, ["науков", "княгині ольги", "аквапарк", "тролейбусн", "стрийськ"]),
]

LVIV_EPICENTR: List[Tuple[str, float, float, List[str]]] = [
    ("Епіцентр (вул. Городоцька, 302)", 49.8265, 23.9410, ["городоцьк", "патона", "мотозавод", "зимновод", "скриня", "вокзал"]),
    ("Епіцентр (вул. Б. Хмельницького, 188а — АС-2)", 49.8655, 24.0468, ["хмельницького", "ас2", "автостанція 2", "промислов", "галицьке", "липинського"]),
    ("Епіцентр (вул. Стрийська, 30 — Кінг Крос)", 49.7760, 24.0135, ["стрийськ", "кінг крос", "ашан", "автовокзал", "науков", "сокільник"]),
]

RE_MCDONALDS = re.compile(
    r"(?<![а-яіїєґa-z])(мак|маку|мака|маці|маком|макдональд[сз][ауіом]?|mc\s*donald'?s?)(?![а-яіїєґa-z])",
    re.IGNORECASE
)
RE_ARSEN = re.compile(
    r"(?<![а-яіїєґa-z])(арсен|арсену|арсені|арсена)(?![а-яіїєґa-z])",
    re.IGNORECASE
)
RE_EPICENTR = re.compile(
    r"(?<![а-яіїєґa-z])(епіцентр|епіцентру|епіцентрі|епіцентра)(?![а-яіїєґa-z])",
    re.IGNORECASE
)


def _dist_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    return 2.0 * R * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def _min_dist_to_route_meters(lat: float, lon: float, route_obj: Optional[Route]) -> float:
    if not route_obj or not route_obj.coordinates:
        return 999999.0
    best = 999999.0
    coords = route_obj.coordinates
    step = 2 if len(coords) > 60 else 1
    for i in range(0, len(coords), step):
        pt = coords[i]
        d = _dist_meters(lat, lon, pt[1], pt[0])
        if d < best:
            best = d
    return best


def resolve_contextual_chain(
    branches: List[Tuple[str, float, float, List[str]]],
    clean_lower: str,
    route_obj: Optional[Route],
    ref_lat: Optional[float] = None,
    ref_lon: Optional[float] = None,
) -> Tuple[str, float, float]:
    """
    Resolves multi-branch slang like 'мак' (McDonald's), 'арсен', 'епіцентр' to the exact branch
    nearest to the mentioned street/landmark AND/OR along the mentioned transport route.
    """
    # 1. Explicit street/district keyword match in the message text
    keyword_matches = []
    for name, b_lat, b_lon, kw_list in branches:
        for kw in kw_list:
            if kw in clean_lower:
                keyword_matches.append((name, b_lat, b_lon))
                break

    if len(keyword_matches) == 1:
        return keyword_matches[0]
    if len(keyword_matches) > 1 and route_obj and route_obj.coordinates:
        keyword_matches.sort(key=lambda b: _min_dist_to_route_meters(b[1], b[2], route_obj))
        return keyword_matches[0]
    if keyword_matches:
        return keyword_matches[0]

    # 2. If another street/stop coordinate was already identified in the message, pick the branch closest to that street
    if ref_lat is not None and ref_lon is not None:
        scored = []
        for name, b_lat, b_lon, _ in branches:
            d_street = _dist_meters(ref_lat, ref_lon, b_lat, b_lon)
            d_route = _min_dist_to_route_meters(b_lat, b_lon, route_obj) if route_obj else 0.0
            penalty = d_route * 1.5 if (route_obj and d_route > 500) else 0.0
            scored.append((d_street + penalty, name, b_lat, b_lon))
        scored.sort(key=lambda x: x[0])
        return scored[0][1], scored[0][2], scored[0][3]

    # 3. If a transport route is known (e.g. "в 3 трамваї біля маку" or "46 біля маку"), find branches along that route's track
    if route_obj and route_obj.coordinates:
        on_route = []
        for idx, (name, b_lat, b_lon, _) in enumerate(branches):
            d_route = _min_dist_to_route_meters(b_lat, b_lon, route_obj)
            on_route.append((d_route, idx, name, b_lat, b_lon))
        near_route = [item for item in on_route if item[0] <= 550.0]
        if near_route:
            near_route.sort(key=lambda x: (0 if x[0] <= 320.0 else 1, x[1], x[0]))
            best = near_route[0]
            return best[2], best[3], best[4]
        on_route.sort(key=lambda x: x[0])
        best = on_route[0]
        return best[2], best[3], best[4]

    # 4. Default fallback (central Lviv branch)
    return branches[-1][0], branches[-1][1], branches[-1][2]


# Number words to digits (almost always trams in Lviv slang)
NUMBER_WORDS: Dict[str, Tuple[str, TransportType]] = {
    "одиничку": ("1", TransportType.TRAM),
    "одиниці": ("1", TransportType.TRAM),
    "одиничка": ("1", TransportType.TRAM),
    "двойку": ("2", TransportType.TRAM),
    "двійку": ("2", TransportType.TRAM),
    "двійці": ("2", TransportType.TRAM),
    "двійка": ("2", TransportType.TRAM),
    "трійку": ("3", TransportType.TRAM),
    "трійці": ("3", TransportType.TRAM),
    "трійка": ("3", TransportType.TRAM),
    "тройку": ("3", TransportType.TRAM),
    "четвірку": ("4", TransportType.TRAM),
    "четвірці": ("4", TransportType.TRAM),
    "четвірка": ("4", TransportType.TRAM),
    "п'ятірку": ("5", TransportType.BUS),
    "пятірку": ("5", TransportType.BUS),
    "шістку": ("6", TransportType.TRAM),
    "шістці": ("6", TransportType.TRAM),
    "шістка": ("6", TransportType.TRAM),
    "сімку": ("7", TransportType.TRAM),
    "сімці": ("7", TransportType.TRAM),
    "сімка": ("7", TransportType.TRAM),
    "вісімку": ("8", TransportType.TRAM),
    "вісімці": ("8", TransportType.TRAM),
    "вісімка": ("8", TransportType.TRAM),
    "дев'ятку": ("9", TransportType.TRAM),
    "девятку": ("9", TransportType.TRAM),
    "дев'ятці": ("9", TransportType.TRAM),
    "дев'ятка": ("9", TransportType.TRAM),
    "десятку": ("10", TransportType.BUS),
}

# Regex patterns for Ukrainian transit reports (including ІіЇїЄєҐґ)
UKR_CHARS = r"А-Яа-яІіЇїЄєҐґA-Za-z0-9’'\-"

# Strict filter for military/mobilization/TCC messages (Art. 114-1/114-2 Criminal Code of Ukraine)
RE_MILITARY_FILTER = re.compile(
    r"\b(тцк|повістк[а-яіїєґ]*|повестк[а-яіїєґ]*|воєнком[а-яіїєґ]*|военком[а-яіїєґ]*|військов[а-яіїєґ]*|оливк[а-яіїєґ]*|піксель|блокпост[а-яіїєґ]*|мобілізац[а-яіїєґ]*|бусифікац[а-яіїєґ]*)\b",
    re.IGNORECASE
)

RE_NOISE = re.compile(
    r"(куп(?:ував|ляв|ив)\s+алкоголь|підкажіть|хто\s+знає|скажіть\s+будь\s*ласка|реклама|знижк|підписуйтесь|шукаю|розіграш)",
    re.IGNORECASE
)

RE_CLEARED = re.compile(
    r"\b(чисто|вийшли\s+вже|вже\s+вийшли|нема|немає|проїхали|контролю\s+нема|відбій)\b",
    re.IGNORECASE
)

RE_EXITED = re.compile(
    r"(?<!не\s)\b(вийшл[аои]|виходять|висадили|зійшл[аои]|вискочил[аои])\b",
    re.IGNORECASE
)

RE_ENTERED = re.compile(
    r"\b(зайшл[аои]|сіл[аои]|сидять|їде|їдуть|виїхали|пересіл[аои]|залізл[аои]|заскочил[аои]|пакують|шмонають|перевіряють|блокують|будуть\s+на\s+\d+\s+сідати)\b",
    re.IGNORECASE
)

RE_DIRECTION = re.compile(
    rf"(?:в\s+сторону|сторону|в\s+сторну|в\s+строну|в\s+сторок|в\s+бік|у\s+бік|в\s+напрямку|напрямок|курсом\s+до|курсом\s+на|до\s+площі|від\s+[{UKR_CHARS}]+\s+на)\s+([{UKR_CHARS}\s\.\(\)]+?)(?:$|[,;!\n]|\bі\s+не\b|\bхз\b)",
    re.IGNORECASE
)

RE_LOCATION = re.compile(
    rf"(?:на\s+зупинці|зупинка|біля\s+зупинки|перехрестя|біля|коло|навпроти|поблизу|від|на)\s+[«\"\']?([{UKR_CHARS}\s\.]+)[»\"\']?",
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

        # Strict 10s deadline and single attempt (no long tenacity backoff loops on 503s)
        client = genai.Client(
            api_key=settings.GEMINI_API_KEY,
            http_options=types.HttpOptions(
                timeout=10000,
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )
        prompt = (
            "You are a public transit inspector sighting extractor for Lviv, Ukraine. "
            "Extract transit inspector sightings from this Telegram message. "
            "Note Lviv slang: 'мак'/'маку' = McDonald's nearest to the mentioned street/route, "
            "'пінгвіни'/'чайки'/'жилетки'/'ревізори' = ticket inspectors, "
            "'рогатий' = trolleybus, 'одиничка'..'дев'ятка' = trams 1..9.\n"
            f"Report text: {text}"
        )
        response = client.models.generate_content(
            model=settings.GEMINI_MODEL or "gemini-3.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_json_schema=LLMSightingResult.model_json_schema(),
                thinking_config=types.ThinkingConfig(thinking_budget=0),
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

        # 3. Extract count of inspectors (including slang like "2 пінгвіни", "3 чайки", "2 жилетки")
        count = 2
        m_cnt = re.search(
            r"\b([1-5])\s*(?:контролер|контролю|чоловік|людин|контр|пінгвін|чайк|шакал|упир|ревізор|жилетк|особ)",
            clean_lower
        )
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
        if re.search(r"\b(трамва[а-яіїєґ]*|травма[а-яіїєґ]*|трам|тм\s*\d+)\b", clean_lower):
            transport_type = TransportType.TRAM
        elif re.search(r"\b(тролейбус[а-яіїєґ]*|трол[іа]?|рогат[ийомауі]+|тр\s*\d+)\b", clean_lower):
            transport_type = TransportType.TROLLEYBUS
        elif re.search(r"\b(автобус[а-яіїєґ]*|маршрутк[а-яіїєґ]*|бус[іау]?|авт)\b", clean_lower):
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
                r"(?:зайшл[аои]\s+(?:в|у)|сіл[аои]\s+(?:в|у)|вийшл[аои]\s+(?:з|із)|сидять\s+(?:в|у)|були\s+(?:в|у)|їд(?:е|уть)\s+(?:в|у)|як\s+їде|контроль\s+(?:у|в|на)?|будуть\s+на)\s+(\d{1,2}[а-яіїєa-z]?)\b",
                r"(?:трамва[а-яіїєґ]*|травма[а-яіїєґ]*|автобус[а-яіїєґ]*|тролейбус[а-яіїєґ]*|рогат[а-яіїєґ]*|маршрутк[а-яіїєґ]*|маршрут[а-яіїєґ]*)\s*(?:№|#)?\s*(\d{1,2}[а-яіїєa-z]?)\b",
                r"\b(\d{1,2}[а-яіїєa-z]?)\s*(?:трамва|травма|автобус|тролейбус|рогат|маршрутк)",
                r"\b(?:в|у|на)\s+(\d{1,2}[а-яіїєa-z]?)\b(?!\s*(?:контролер|чоловік|людин|пінгвін|чайк|борт|годин|ранку|хв))",
            ]
            for pat in route_patterns:
                m = re.search(pat, clean_lower)
                if m:
                    cand = m.group(1).strip()
                    if re.search(rf"(?:автостанці[яї]|ас|номер)\s*[\-]?\s*{re.escape(cand)}\b", clean_lower):
                        continue
                    route_raw = cand
                    if action == SightingAction.STANDING and re.search(rf"\b(?:в|у)\s+{re.escape(cand)}\b", clean_lower):
                        action = SightingAction.IN_VEHICLE
                    break

        # Match route against GTFS routes
        route_obj = None
        if route_raw:
            if transport_type == TransportType.UNKNOWN and route_raw in {"1", "2", "3", "4", "6", "7", "8", "9"}:
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

        # Sort aliases by length descending so more specific aliases match first
        sorted_aliases = sorted(KNOWN_ALIASES.items(), key=lambda x: len(x[0]), reverse=True)

        # First search aliases in primary text (outside direction clause)
        for alias, (alias_name, a_lat, a_lon) in sorted_aliases:
            if alias in ("сихова", "сихові", "сихів", "центру", "центрі", "центр"):
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
                raw_loc = re.split(
                    r'\b(в\s+сторону|в\s+бік|у\s+бік|в\s+напрямку|як\s+їде|сіли|стоять|стоїть|зайшли|вийшли|контролер[иів]?|пінгвін[иів]?|двоє|троє)\b',
                    raw_loc
                )[0].strip()
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

        # If still not found, scan words in primary text
        if not stop_lat:
            stop_ignore_words = {
                "контролери", "контролера", "контролер", "контроль", "пінгвіни", "пінгвіна",
                "чайки", "шакали", "ревізори", "жилетки", "зайшли", "вийшли",
                "сіли", "сидять", "їдуть", "стоять", "стоїть", "трамвай", "трамваї", "трамвайній",
                "тролейбус", "тролейбусі", "тролейбусній", "автобус", "автобусі", "маршрутка", "маршрутці",
                "сторону", "напрямку", "поліцією", "поліція", "чоловік", "людини", "двоє", "троє",
                "маку", "мака", "макдональдс", "арсен", "арсену", "епіцентр", "епіцентру"
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

        # 8. Context-aware multi-branch slang resolution: "мак" (McDonald's), "арсен" (Arsen), "епіцентр" (Epicentr)
        # Uses primary_text_lower (excluding direction destination) + matched street coords + route geometry!
        if RE_MCDONALDS.search(primary_text_lower) or (not stop_lat and RE_MCDONALDS.search(clean_lower)):
            mc_name, mc_lat, mc_lon = resolve_contextual_chain(
                LVIV_MCDONALDS, primary_text_lower, route_obj, stop_lat, stop_lon
            )
            location_text = mc_name
            matched_stop_name = mc_name
            stop_lat = mc_lat
            stop_lon = mc_lon
        elif RE_ARSEN.search(primary_text_lower) or (not stop_lat and RE_ARSEN.search(clean_lower)):
            ar_name, ar_lat, ar_lon = resolve_contextual_chain(
                LVIV_ARSEN, primary_text_lower, route_obj, stop_lat, stop_lon
            )
            location_text = ar_name
            matched_stop_name = ar_name
            stop_lat = ar_lat
            stop_lon = ar_lon
        elif RE_EPICENTR.search(primary_text_lower) or (not stop_lat and RE_EPICENTR.search(clean_lower)):
            ep_name, ep_lat, ep_lon = resolve_contextual_chain(
                LVIV_EPICENTR, primary_text_lower, route_obj, stop_lat, stop_lon
            )
            location_text = ep_name
            matched_stop_name = ep_name
            stop_lat = ep_lat
            stop_lon = ep_lon

        # Fallback: check aliases in the entire text (including direction hint if no origin stop was specified)
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
                confidence = 0.94
            elif matched_stop_name:
                confidence = 0.82
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
