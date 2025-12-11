import asyncio
import logging
import sqlite3
import html
from datetime import date, datetime, timedelta

from aiogram import Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.exceptions import TelegramNetworkError

from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

# ================= НАСТРОЙКИ =================

API_TOKEN = "8434810807:AAHt639Hf4s2MjbybBkZvFD1oDBkng2n-xA"
DB_NAME = "habits.db"

logging.basicConfig(level=logging.INFO)

bot = Bot(
    token=API_TOKEN,
    default=DefaultBotProperties(parse_mode="HTML")
)
dp = Dispatcher()


# ================= СОСТОЯНИЯ (FSM) =================

class AddHabitState(StatesGroup):
    waiting_for_name = State()
    waiting_for_time = State()


# ================= БАЗА ДАННЫХ =================

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # Таблица привычек с полем reminder_time
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS habits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            reminder_time TEXT
        )
        """
    )

    # Таблица выполнений
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS completions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            habit_id INTEGER NOT NULL,
            done_date DATE NOT NULL,
            UNIQUE(habit_id, done_date)
        )
        """
    )

    conn.commit()
    conn.close()


def add_habit(user_id: int, name: str, reminder_time: str | None):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO habits (user_id, name, reminder_time) VALUES (?, ?, ?)",
        (user_id, name, reminder_time),
    )
    conn.commit()
    conn.close()


def deactivate_habit(user_id: int, habit_id: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE habits SET is_active = 0 WHERE id = ? AND user_id = ?",
        (habit_id, user_id),
    )
    conn.commit()
    conn.close()


def get_habits(user_id: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, name FROM habits WHERE user_id = ? AND is_active = 1",
        (user_id,),
    )
    rows = cursor.fetchall()
    conn.close()
    return rows


def get_habits_for_time(reminder_time: str):
    """
    Вернуть (user_id, name) всех активных привычек с заданным временем напоминания.
    """
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT user_id, name
        FROM habits
        WHERE is_active = 1 AND reminder_time = ?
        """,
        (reminder_time,),
    )
    rows = cursor.fetchall()
    conn.close()
    return rows


def mark_done(habit_id: int, day: date):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT OR IGNORE INTO completions (habit_id, done_date) VALUES (?, ?)",
            (habit_id, day.isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def get_habit_streak(habit_id: int) -> int:
    """
    Возвращает текущий стрик (серия дней подряд до сегодня) для привычки.
    """
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT done_date
        FROM completions
        WHERE habit_id = ?
        ORDER BY done_date DESC
        """,
        (habit_id,),
    )
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return 0

    dates = [date.fromisoformat(r[0]) for r in rows]

    streak = 0
    current_day = date.today()

    for d in dates:
        if d == current_day:
            streak += 1
            current_day = current_day - timedelta(days=1)
        else:
            break

    return streak


def get_stats(user_id: int, days: int = 7):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT h.id, h.name,
               COUNT(c.id) AS total_done
        FROM habits h
        LEFT JOIN completions c ON h.id = c.habit_id
        WHERE h.user_id = ? AND h.is_active = 1
        GROUP BY h.id, h.name
        ORDER BY h.id
        """,
        (user_id,),
    )
    total_rows = cursor.fetchall()

    since = (date.today() - timedelta(days=days)).isoformat()
    cursor.execute(
        """
        SELECT h.id, h.name,
               COUNT(c.id) AS recent_done
        FROM habits h
        LEFT JOIN completions c
          ON h.id = c.habit_id AND c.done_date >= ?
        WHERE h.user_id = ? AND h.is_active = 1
        GROUP BY h.id, h.name
        ORDER BY h.id
        """,
        (since, user_id),
    )
    recent_rows = cursor.fetchall()
    conn.close()

    recent_map = {row[0]: row[2] for row in recent_rows}

    stats = []
    for habit_id, name, total_done in total_rows:
        recent_done = recent_map.get(habit_id, 0)
        stats.append((habit_id, name, total_done, recent_done))

    return stats


# ================= КЛАВИАТУРА =================

def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="➕ Добавить привычку"),
                KeyboardButton(text="📋 Мои привычки"),
            ],
            [
                KeyboardButton(text="✅ Отметить выполнение"),
                KeyboardButton(text="📊 Статистика"),
            ],
            [
                KeyboardButton(text="🗑 Удалить привычку"),
            ],
        ],
        resize_keyboard=True,
    )


# ================= ХЕНДЛЕРЫ =================

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    logging.info(f"/start from {message.from_user.id}")

    text = (
        "<b>Трекер привычек</b>\n\n"
        "Бот помогает фиксировать привычки, получать напоминания "
        "и отслеживать прогресс.\n\n"
        "<b>Основные действия:</b>\n"
        "• добавить привычку и время напоминания\n"
        "• отмечать выполнение за сегодня\n"
        "• просматривать список и статистику\n\n"
        "Выберите действие с помощью кнопок ниже."
    )
    await message.answer(text, reply_markup=main_keyboard())


# ----- Добавление привычки (диалог: название + время) -----

@dp.message(Command("addhabit"))
async def cmd_addhabit(message: types.Message, state: FSMContext):
    await message.answer(
        "<b>Добавление привычки</b>\n\n"
        "Какую привычку вы хотите добавить?\n"
        "Напишите её название одним сообщением."
    )
    await state.set_state(AddHabitState.waiting_for_name)


@dp.message(F.text == "➕ Добавить привычку")
async def addhabit_button(message: types.Message, state: FSMContext):
    await cmd_addhabit(message, state)


@dp.message(AddHabitState.waiting_for_name)
async def habit_name_received(message: types.Message, state: FSMContext):
    habit_name = message.text.strip()

    if not habit_name:
        await message.answer("Название не может быть пустым. Введите название привычки ещё раз.")
        return

    await state.update_data(habit_name=habit_name)

    await message.answer(
        "Укажите время напоминания для этой привычки.\n"
        "Формат: <code>ЧЧ:ММ</code>, например <code>09:30</code>."
    )
    await state.set_state(AddHabitState.waiting_for_time)


@dp.message(AddHabitState.waiting_for_time)
async def habit_time_received(message: types.Message, state: FSMContext):
    raw_time = message.text.strip()

    try:
        # Разбор и нормализация времени
        parsed = datetime.strptime(raw_time, "%H:%M")
        reminder_time = parsed.strftime("%H:%M")
    except ValueError:
        await message.answer(
            "Некорректный формат времени.\n"
            "Пожалуйста, введите время в формате <code>ЧЧ:ММ</code>, например <code>09:30</code>."
        )
        return

    data = await state.get_data()
    habit_name = data.get("habit_name")

    if not habit_name:
        await message.answer("Произошла ошибка при сохранении привычки. Попробуйте ещё раз.")
        await state.clear()
        return

    add_habit(message.from_user.id, habit_name, reminder_time)

    await message.answer(
        f"Привычка <b>{html.escape(habit_name)}</b> добавлена.\n"
        f"Напоминание будет приходить каждый день в <code>{reminder_time}</code>.",
        reply_markup=main_keyboard(),
    )

    await state.clear()


# ----- Список привычек -----

@dp.message(Command("listhabits"))
async def cmd_listhabits(message: types.Message):
    logging.info(f"/listhabits from {message.from_user.id}")
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT name, reminder_time FROM habits WHERE user_id = ? AND is_active = 1",
        (message.from_user.id,),
    )
    habits = cursor.fetchall()
    conn.close()

    if not habits:
        await message.answer(
            "<b>Ваши привычки</b>\n\n"
            "Сейчас у вас нет активных привычек.\n"
            "Нажмите «➕ Добавить привычку», чтобы создать первую.",
            reply_markup=main_keyboard(),
        )
        return

    lines = ["<b>Ваши активные привычки:</b>\n"]
    for idx, (name, reminder_time) in enumerate(habits, start=1):
        safe_name = html.escape(name)
        if reminder_time:
            lines.append(f"{idx}. {safe_name} — напоминание в <code>{reminder_time}</code>")
        else:
            lines.append(f"{idx}. {safe_name} — напоминание не задано")

    lines.append("\nДля удаления используйте кнопку «🗑 Удалить привычку».")
    await message.answer("\n".join(lines), reply_markup=main_keyboard())


@dp.message(F.text == "📋 Мои привычки")
async def listhabits_button(message: types.Message):
    await cmd_listhabits(message)


# ----- Отметка выполнения -----

@dp.message(Command("done"))
async def cmd_done(message: types.Message):
    logging.info(f"/done from {message.from_user.id}")
    habits = get_habits(message.from_user.id)
    if not habits:
        await message.answer(
            "У вас нет активных привычек для отметки.\n"
            "Сначала добавьте привычку через «➕ Добавить привычку».",
            reply_markup=main_keyboard(),
        )
        return

    kb = InlineKeyboardBuilder()
    for habit_id, name in habits:
        safe_name = html.escape(name)
        kb.button(
            text=safe_name,
            callback_data=f"done:{habit_id}",
        )
    kb.adjust(1)

    await message.answer(
        "Выберите привычку, которую вы <b>выполнили сегодня</b>:",
        reply_markup=kb.as_markup(),
    )


@dp.message(F.text == "✅ Отметить выполнение")
async def done_button(message: types.Message):
    await cmd_done(message)


@dp.callback_query(F.data.startswith("done:"))
async def callback_done(callback: types.CallbackQuery):
    logging.info(f"callback {callback.data!r} from {callback.from_user.id}")
    try:
        habit_id_str = callback.data.split(":", 1)[1]
        habit_id = int(habit_id_str)
    except Exception:
        await callback.answer("Ошибка данных.", show_alert=True)
        return

    mark_done(habit_id, date.today())
    await callback.answer("Выполнение сохранено.", show_alert=False)
    await callback.message.edit_text("Отметка выполнения сохранена.")


# ----- Удаление привычек -----

@dp.message(Command("deletehabit"))
async def cmd_deletehabit(message: types.Message):
    logging.info(f"/deletehabit from {message.from_user.id}")
    habits = get_habits(message.from_user.id)
    if not habits:
        await message.answer(
            "У вас нет активных привычек.\n"
            "Добавьте привычку через «➕ Добавить привычку».",
            reply_markup=main_keyboard(),
        )
        return

    kb = InlineKeyboardBuilder()
    for habit_id, name in habits:
        safe_name = html.escape(name)
        kb.button(
            text=f"Удалить: {safe_name}",
            callback_data=f"del:{habit_id}",
        )
    kb.adjust(1)

    await message.answer(
        "<b>Удаление привычек</b>\n"
        "Выберите привычку, которую нужно убрать из списка активных:",
        reply_markup=kb.as_markup(),
    )


@dp.message(F.text == "🗑 Удалить привычку")
async def deletehabit_button(message: types.Message):
    await cmd_deletehabit(message)


@dp.callback_query(F.data.startswith("del:"))
async def callback_delete_habit(callback: types.CallbackQuery):
    logging.info(f"delete callback {callback.data!r} from {callback.from_user.id}")
    try:
        habit_id_str = callback.data.split(":", 1)[1]
        habit_id = int(habit_id_str)
    except Exception:
        await callback.answer("Ошибка данных.", show_alert=True)
        return

    deactivate_habit(callback.from_user.id, habit_id)
    await callback.answer("Привычка удалена.", show_alert=False)
    await callback.message.edit_text("Привычка удалена из списка активных.")


# ----- Статистика -----

@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    logging.info(f"/stats from {message.from_user.id}")
    stats = get_stats(message.from_user.id, days=7)

    if not stats:
        await message.answer(
            "Статистика пока пуста.\n"
            "Добавьте привычку и отметьте её выполнение, "
            "чтобы здесь появились данные.",
            reply_markup=main_keyboard(),
        )
        return

    lines = ["<b>Статистика по привычкам:</b>\n"]
    for habit_id, name, total_done, recent_done in stats:
        safe_name = html.escape(name)
        streak = get_habit_streak(habit_id)
        lines.append(
            f"<b>{safe_name}</b>\n"
            f"— всего выполнений: <code>{total_done}</code>\n"
            f"— за последние 7 дней: <code>{recent_done}</code>\n"
            f"— текущая серия: <code>{streak}</code> дн.\n"
        )

    await message.answer("\n".join(lines), reply_markup=main_keyboard())


@dp.message(F.text == "📊 Статистика")
async def stats_button(message: types.Message):
    await cmd_stats(message)


# ----- Fallback -----

@dp.message()
async def fallback(message: types.Message):
    logging.info(f"UNHANDLED message: {message.text!r} from {message.from_user.id}")
    await message.answer(
        "Команда не распознана.\n"
        "Используйте кнопки внизу экрана или команду <code>/start</code>, "
        "чтобы увидеть доступные действия.",
        reply_markup=main_keyboard(),
    )


# ================= ФОНОВЫЙ ПРОЦЕСС НАПОМИНАНИЙ =================

async def reminders_worker():
    """
    Каждую минуту проверяет, не наступило ли время напоминаний,
    и отправляет сообщения пользователям.
    """
    logging.info("Фоновый процесс напоминаний запущен.")
    while True:
        now = datetime.now()
        current_time = now.strftime("%H:%M")

        habits = get_habits_for_time(current_time)
        if habits:
            logging.info(f"Отправка напоминаний для времени {current_time}, записей: {len(habits)}")
        for user_id, habit_name in habits:
            try:
                await bot.send_message(
                    user_id,
                    f"Напоминание по привычке: <b>{html.escape(habit_name)}</b>.\n"
                    f"Не забудьте выполнить её сегодня.",
                )
            except Exception as e:
                logging.warning(f"Не удалось отправить напоминание пользователю {user_id}: {e}")

        await asyncio.sleep(60)


# ================= ЗАПУСК =================

async def main():
    init_db()
    # запускаем фоновую задачу напоминаний
    asyncio.create_task(reminders_worker())

    while True:
        try:
            logging.info("Запускаю polling...")
            await dp.start_polling(bot)
        except TelegramNetworkError as e:
            logging.warning(
                f"Сетевая ошибка Telegram: {e}. "
                f"Повторная попытка через 5 секунд."
            )
            await asyncio.sleep(5)
        except Exception as e:
            logging.exception(f"Неожиданная ошибка, бот остановлен: {e}")
            break


if __name__ == "__main__":
    asyncio.run(main())
