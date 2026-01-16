import os
import asyncio
import random
import re
from dataclasses import dataclass
from typing import Optional, Literal

import aiohttp
from bs4 import BeautifulSoup
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
BASE = "https://transphoto.org"

# 👉 ВОТ СЮДА ВСТАВЛЯЕШЬ ТОКЕН
BOT_TOKEN = "8565327314:AAGu5sVapj_rYklmYeHoX-uHxB7ni2m8Bdg"

# 👉 ВОТ СЮДА ВСТАВЛЯЕШЬ COOKIE
TRANSPHOTO_COOKIE = "_ga=...; _ga_FSVJTB6RNR=...; _ym_d=...; _ym_isad=...; _ym_uid=...; cf_clearance=..."

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN not set")

TransportKind = Literal["tram", "trolley", "any"]

@dataclass
class VehicleResult:
    vehicle_url: str
    title: str
    info_text: str
    photo_url: Optional[str]
    photo_page: Optional[str]


def main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🚎 Случайный троллейбус", callback_data="rnd:trolley"),
            InlineKeyboardButton(text="🚋 Случайный трамвай", callback_data="rnd:tram"),
        ],
        [
            InlineKeyboardButton(text="🎲 Случайный транспорт", callback_data="rnd:any"),
        ],
        [
            InlineKeyboardButton(text="🍀 Мне повезёт (бортовой номер)", callback_data="lucky"),
        ],
    ])
def headers(use_cookie: bool = False) -> dict:
    h = {
        "User-Agent": "Mozilla/5.0 (TrollDriveBot/1.0)",
        "Accept-Language": "ru,en;q=0.9",
    }
    if use_cookie and TRANSPHOTO_COOKIE:
        h["Cookie"] = TRANSPHOTO_COOKIE
    return h


def abs_url(url: str) -> str:
    if not url:
        return url
    if url.startswith("http"):
        return url
    if url.startswith("/"):
        return BASE + url
    return BASE + "/" + url


async def fetch_html(session: aiohttp.ClientSession, url: str, use_cookie: bool = False) -> str:
    async with session.get(url, headers=headers(use_cookie), timeout=aiohttp.ClientTimeout(total=30)) as r:
        r.raise_for_status()
        return await r.text()


def pick_og_image(soup: BeautifulSoup) -> Optional[str]:
    tag = soup.find("meta", property="og:image")
    if tag and tag.get("content"):
        return abs_url(tag["content"])
    return None


def pick_vehicle_link_from_photo_page(soup: BeautifulSoup) -> Optional[str]:
    a = soup.select_one('a[href^="/vehicle/"]')
    if a and a.get("href"):
        return abs_url(a["href"])
    return None


def detect_kind_from_title(title: str) -> str:
    t = title.lower()
    if "trolleybus" in t or "троллейбус" in t:
        return "trolley"
    if "tram" in t or "tramway" in t or "трамвай" in t:
        return "tram"
    return "any"
def parse_vehicle_info(vehicle_soup: BeautifulSoup) -> tuple[str, str]:
    # Заголовок
    h1 = vehicle_soup.find("h1")
    title = h1.get_text(" ", strip=True) if h1 else "Transport"

    # Берём текст страницы и вытаскиваем нужные поля (как на “фото 2”)
    page_text = vehicle_soup.get_text("\n", strip=True)

    keys = [
        ("City:", "Город:"),
        ("Location:", "Локация:"),
        ("Operator/Facility:", "Депо/Парк:"),
        ("Model:", "Модель:"),
        ("Built:", "Построен:"),
        ("Serial number:", "Заводской №:"),
        ("VIN:", "VIN:"),
        ("Current state:", "Текущее состояние:"),
        ("Purpose:", "Назначение:"),
    ]

    lines = [f"**{title}**", ""]
    for en, ru in keys:
        m = re.search(rf"^{re.escape(en)}\s*(.+)$", page_text, flags=re.MULTILINE)
        label = en
        if not m:
            m = re.search(rf"^{re.escape(ru)}\s*(.+)$", page_text, flags=re.MULTILINE)
            label = ru
        if m:
            lines.append(f"{label} {m.group(1).strip()}")

    return title, "\n".join(lines).strip()


async def random_photo_page(session: aiohttp.ClientSession) -> str:
    # Берём главную страницу и выбираем случайную ссылку на /photo/
    home_html = await fetch_html(session, BASE + "/")
    soup = BeautifulSoup(home_html, "html.parser")
    links = [abs_url(a["href"]) for a in soup.select('a[href^="/photo/"]') if a.get("href")]
    if not links:
        raise RuntimeError("Не нашла ссылки /photo/ на главной")
    return random.choice(links)


async def get_random_vehicle(session: aiohttp.ClientSession, kind: TransportKind) -> VehicleResult:
    # Пытаемся несколько раз, пока не попадём в нужный тип (трам/тролл)
    for _ in range(25):
        photo_page = await random_photo_page(session)
        photo_html = await fetch_html(session, photo_page)
        photo_soup = BeautifulSoup(photo_html, "html.parser")

        vehicle_url = pick_vehicle_link_from_photo_page(photo_soup)
        if not vehicle_url:
            continue

        vehicle_html = await fetch_html(session, vehicle_url)
        vehicle_soup = BeautifulSoup(vehicle_html, "html.parser")
        title, info_text = parse_vehicle_info(vehicle_soup)

        detected = detect_kind_from_title(title)
        if kind != "any" and detected != kind:
            continue

        # Фото: берём og:image со страницы фото
        photo_direct = pick_og_image(photo_soup)

        # Добавим ссылки в конец текста
        info_text = info_text + f"\n\nСсылка: {vehicle_url}\nФото: {photo_page}"

        return VehicleResult(
            vehicle_url=vehicle_url,
            title=title,
            info_text=info_text,
            photo_url=photo_direct,
            photo_page=photo_page
        )

    raise RuntimeError("Не получилось подобрать случайный транспорт, попробуй ещё раз")
bot = Bot(BOT_TOKEN, parse_mode="Markdown")
dp = Dispatcher()


@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer("TrollDriveBot 🚎🚋\nВыбирай кнопку:", reply_markup=main_keyboard())


@dp.callback_query(F.data.startswith("rnd:"))
async def cb_random(callback):
    kind = callback.data.split(":", 1)[1]  # trolley / tram / any
    await callback.message.answer("Ищу транспорт…")

    async with aiohttp.ClientSession() as session:
        vr = await get_random_vehicle(session, kind=kind)  # type: ignore

    if vr.photo_url:
        await callback.message.answer_photo(vr.photo_url, caption=vr.info_text)
    else:
        await callback.message.answer(vr.info_text)

    await callback.answer()


@dp.callback_query(F.data == "lucky")
async def cb_lucky(callback: CallbackQuery):
    await callback.message.answer("Введи бортовой номер (например 6845):")
    await callback.answer()


@dp.message()
async def msg_board_number(message: Message):
    text = (message.text or "").strip()

    # Пока: просто проверяем, что это число. Реальный поиск по номеру добавим следующим шагом.
    if not text.isdigit():
        return

    await message.answer(
        f"Ок, номер **{text}** принят.\n"
        f"Дальше подключим поиск по этому номеру через cookies.",
        reply_markup=main_keyboard()
    )


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
