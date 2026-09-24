"""Выдуманные данные для демо. Никаких реальных аккаунтов — всё живёт в памяти процесса."""
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

TZ = timezone(timedelta(hours=11))  # часовой пояс демо

STATUS = {
    "online": ("#34D399", "Онлайн", "🟢"),
    "need_login": ("#F5A623", "Нужен вход", "🟠"),
    "offline": ("#5B6273", "Офлайн", "⚫"),
}
STYLES = ["g1", "g2", "sv", "st"]


def now_hm() -> str:
    return datetime.now(TZ).strftime("%H:%M")


@dataclass
class Msg:
    me: bool
    who: str
    time: str
    text: str


@dataclass
class Chat:
    id: str
    title: str
    kind: str  # user | group | channel
    msgs: list[Msg]
    unread: int = 0
    muted: bool = False
    members: int = 0

    @property
    def last(self) -> Msg | None:
        return self.msgs[-1] if self.msgs else None


@dataclass
class Account:
    id: int
    name: str
    username: str
    phone: str
    status: str
    style: str
    chats: dict[str, Chat] = field(default_factory=dict)
    contacts: list[str] = field(default_factory=list)
    notify: bool = True

    @property
    def letter(self) -> str:
        return self.name[0].upper()

    @property
    def status_color(self) -> str:
        return STATUS[self.status][0]

    @property
    def status_label(self) -> str:
        return STATUS[self.status][1]

    @property
    def status_emoji(self) -> str:
        return STATUS[self.status][2]

    @property
    def phone_masked(self) -> str:
        digits = "".join(c for c in self.phone if c.isdigit())
        return f"+{digits[0]} ••• ••• {digits[-4:-2]} {digits[-2:]}"

    def by_kind(self, kinds: tuple[str, ...]) -> list[Chat]:
        items = [c for c in self.chats.values() if c.kind in kinds]
        return sorted(items, key=lambda c: (c.unread == 0, c.title))

    @property
    def unread_total(self) -> int:
        return sum(c.unread for c in self.chats.values() if not c.muted)


PEOPLE = ["Иван", "Мама", "Дима", "Катя", "Никита", "Лёша", "Аня", "Паша", "Оля",
          "Серёга", "Вика", "Егор", "Тимур", "Настя", "Кирилл", "Женя", "Рома", "Лиза"]

DIALOGS = [
    [(0, "Привет, как дела?"), (1, "Норм, ты как?"), (0, "Тоже норм. Завтра в силе?"),
     (1, "Да, к шести подойду"), (0, "Ок, возьми зарядку"), (1, "Возьму 👍"), (0, "Кстати, скинь фотки с выходных")],
    [(0, "Ты дома?"), (1, "Скоро буду"), (0, "Купи хлеб и молоко пожалуйста"), (1, "Ладно"),
     (0, "И на ужин что-нибудь"), (1, "Пельмени?"), (0, "Давай")],
    [(1, "Сервер опять лёг?"), (0, "Не, просто рестарт был"), (1, "А, ок"), (0, "Через 5 минут поднимется"),
     (1, "Понял, спасибо"), (0, "Если что — пиши")],
    [(0, "Смотрел новый трейлер?"), (1, "Ещё нет, норм?"), (0, "Огонь, скину ссылку"), (1, "Давай"),
     (0, "Держи, в конце самое интересное"), (1, "Щас гляну")],
    [(0, "Домашку по алгебре сделал?"), (1, "Почти, 5-й номер не выходит"), (0, "Там через дискриминант"),
     (1, "Точно, спасибо"), (0, "Завтра контрольная кстати"), (1, "Помню(")],
    [(1, "Го в выходные на рыбалку"), (0, "Если погода будет"), (1, "Обещают солнце"),
     (0, "Тогда го"), (1, "В 7 утра выезжаем"), (0, "Жёстко, но ладно")],
    [(0, "Можешь помочь с настройкой роутера?"), (1, "Могу, что там?"), (0, "Интернет пропадает каждые полчаса"),
     (1, "Прошивку обновлял?"), (0, "Нет"), (1, "Начни с этого"), (0, "Ок, попробую вечером")],
    [(0, "Спасибо за вчера!"), (1, "Да не за что"), (0, "Было круто"), (1, "Повторим как-нибудь")],
]

GROUPS = [
    ("Factorio", 182, ["Дима", "Егор", "Паша"], ["Кто на сервер вечером?", "Я в 9 зайду", "Опять байтеры сломались", "Кто строил главную шину??", "Я, а что", "Она через всю карту идёт 😂"]),
    ("Developers", 47, ["Кирилл", "Рома", "Лиза"], ["Кто ревьюнет PR?", "Скинь ссылку", "Готово, пара замечаний", "Деплой в пятницу — плохая идея", "Классика"]),
    ("Друзья", 9, ["Катя", "Никита", "Оля"], ["Где собираемся?", "У меня можно", "Ок, в 7?", "Беру пиццу", "Я колу"]),
    ("11 «А»", 26, ["Настя", "Тимур", "Вика"], ["Что задали по физике?", "§14, задачи 3–7", "Спасибо!", "Завтра классный час перенесли", "На когда?"]),
    ("Project", 5, ["Женя", "Серёга"], ["Созвон в 18:00", "Я опоздаю минут на 10", "Ок, начнём без тебя", "Макет обновил, гляньте"]),
]

CHANNELS = [
    ("Техно-дайджест", ["Вышла новая версия Python", "Обзор лучших терминалов года", "Как устроен QUIC — простыми словами"]),
    ("Город сегодня", ["Паром отменён из-за шторма", "В центре открыли новый сквер", "Прогноз: к выходным до +15"]),
    ("Музыка 🎧", ["Плейлист недели", "Новый альбом уже на площадках", "Лучшее за месяц"]),
]


def _times(n: int, rnd: random.Random) -> list[str]:
    t = datetime.now(TZ).replace(second=0, microsecond=0) - timedelta(minutes=rnd.randint(20, 600))
    out = []
    for _ in range(n):
        out.append(t.strftime("%H:%M"))
        t += timedelta(minutes=rnd.randint(1, 9))
    return out


def _build_chats(acc_id: int) -> tuple[dict[str, Chat], list[str]]:
    rnd = random.Random(acc_id * 7919)
    chats: dict[str, Chat] = {}
    people = rnd.sample(PEOPLE, 13)
    for i, name in enumerate(people):
        script = rnd.choice(DIALOGS)
        times = _times(len(script), rnd)
        msgs = [Msg(bool(me), "Вы" if me else name, times[k], text) for k, (me, text) in enumerate(script)]
        unread = rnd.choice([0, 0, 0, 0, 1, 2]) if not msgs[-1].me else 0
        chats[f"u{i}"] = Chat(f"u{i}", name, "user", msgs, unread)
    for i, (title, members, who, lines) in enumerate(rnd.sample(GROUPS, 4)):
        times = _times(len(lines), rnd)
        msgs = [Msg(False, rnd.choice(who), times[k], text) for k, text in enumerate(lines)]
        chats[f"g{i}"] = Chat(f"g{i}", title, "group", msgs, rnd.choice([0, 0, 2, 4]), members=members)
    for i, (title, posts) in enumerate(rnd.sample(CHANNELS, 2)):
        times = _times(len(posts), rnd)
        msgs = [Msg(False, title, times[k], text) for k, text in enumerate(posts)]
        chats[f"k{i}"] = Chat(f"k{i}", title, "channel", msgs, rnd.choice([0, 1, 3]), muted=rnd.random() < .5, members=rnd.randint(800, 40000))
    contacts = sorted(people[:9] + rnd.sample([p for p in PEOPLE if p not in people], 3))
    return chats, contacts


def _account(acc_id: int, name: str, username: str, phone: str, status: str) -> Account:
    chats, contacts = _build_chats(acc_id)
    return Account(acc_id, name, username, phone, status, STYLES[(acc_id - 1) % 4], chats, contacts)


ACCOUNTS: dict[int, Account] = {
    a.id: a for a in [
        _account(1, "Алекс", "alex_hub", "+7 000 755 12 01", "online"),
        _account(2, "Макс", "account2", "+7 000 310 12 34", "online"),
        _account(3, "Джон", "john_w", "+7 000 082 77 19", "need_login"),
        _account(4, "Саша", "sasha_k", "+7 000 118 40 55", "online"),
    ]
}

NEW_NAMES = [("Ника", "nika_s"), ("Рома", "roma_dev"), ("Лена", "lena_k"), ("Гоша", "gosha_77")]

# «Пользователи бота» — кто к каким аккаунтам имеет доступ (только для экрана доступов)
USERS = {
    1: {"name": "Ты", "admin": True, "roles": {}},
    2: {"name": "@kate_w", "admin": False, "roles": {1: "operator", 2: "viewer"}},
    3: {"name": "@dim4ik", "admin": False, "roles": {4: "operator"}},
}
ROLES = ["none", "viewer", "operator"]
ROLE_LABEL = {"none": "— нет доступа", "viewer": "👁 чтение", "operator": "✍️ чтение и ответы"}

AUDIT: list[tuple[str, str]] = []


def audit(who: str, text: str) -> None:
    AUDIT.append((now_hm(), f"{who} · {text}"))
    del AUDIT[:-50]


def next_account_id() -> int:
    return max(ACCOUNTS, default=0) + 1


def add_account(phone: str) -> Account:
    acc_id = next_account_id()
    name, username = NEW_NAMES[(acc_id - 5) % len(NEW_NAMES)]
    acc = _account(acc_id, name, username, phone, "online")
    ACCOUNTS[acc_id] = acc
    return acc
