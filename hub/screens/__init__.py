"""Экраны бота: у каждого свой модуль и свой роутер.

Порядок проверок на кнопке: Access (кто это, язык) → AccountGate/AdminGate (права) → PinGate (PIN) → хендлер.
fallback — строго последним: ловит устаревшие кнопки и лишние сообщения.
"""
from aiogram import Router

from access import AccountGate
from screens import account, actions, admin, dialog, fallback, home, inbox, lists, login, media, pin, search

USER = Router(name="user")
USER.callback_query.middleware(AccountGate())
USER.callback_query.middleware(pin.PinGate())
USER.include_routers(home.router, account.router, lists.router, dialog.router, actions.router, media.router,
                     search.router, inbox.router)

admin.router.callback_query.middleware(pin.PinGate())   # после AdminGate — он подключён в самом модуле
pin.router.callback_query.middleware(pin.PinGate())     # «Сменить/убрать PIN» — тоже через PIN

ROUTERS = [pin.router, admin.router, login.router, USER, fallback.router]
