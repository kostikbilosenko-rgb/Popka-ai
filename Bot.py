import os
import asyncio
import tempfile
import shutil
from datetime import datetime, timezone

import disnake
from disnake.ext import commands
from huggingface_hub import InferenceClient
from aiohttp import web

# Токени отримуємо із змінних середовища Render
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
HF_TOKEN = os.environ.get("HUGGINGFACE_TOKEN")

client = InferenceClient(token=HF_TOKEN)

MAX_CREDITS = 5
# Модель для генерації відео на Hugging Face (можна змінити на іншу за потреби)
MODEL_NAME = "camenduru/Wan2.1-T2V-1.3B"

user_credits = {}
bot = commands.InteractionBot(intents=disnake.Intents.default())
task_queue = asyncio.Queue()
worker_started = False

def check_and_update_credits(user_id):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if user_id not in user_credits:
        user_credits[user_id] = {"credits": MAX_CREDITS, "last_date": today}
    data = user_credits[user_id]
    if data["last_date"] != today:
        data["credits"] = MAX_CREDITS
        data["last_date"] = today
    return data["credits"]

def hf_generate_video(prompt, out_path):
    # Використовуємо Hugging Face Inference API для генерації відео
    video = client.text_to_video(
        prompt=prompt,
        model=MODEL_NAME
    )
    # Зберігаємо отримане відео у файл
    video.save(out_path)

async def add_watermark_to_video(src, dst, workdir):
    wm_path = "watermark.png"  # Файл водяного знака у репозиторії
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", src, "-i", wm_path,
            "-filter_complex", "overlay=W-w-20:H-h-20",
            "-c:a", "copy", dst,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        if proc.returncode == 0 and os.path.exists(dst):
            return dst
    except Exception as e:
        print("Watermark error:", e)
    return src

async def process_queue():
    loop = asyncio.get_running_loop()
    while True:
        inter, prompt = await task_queue.get()
        workdir = tempfile.mkdtemp()
        try:
            user = inter.author
            uid = user.id
            credits_left = user_credits[uid]["credits"]
            
            await inter.edit_original_response(
                content=f"⏳ Генерація відео через Hugging Face для {user.mention} (осталось кредитов: {credits_left})...\nПромт: `{prompt}`"
            )
            
            raw = os.path.join(workdir, "raw.mp4")
            await loop.run_in_executor(None, hf_generate_video, prompt, raw)
            out = await add_watermark_to_video(raw, os.path.join(workdir, "out.mp4"), workdir)
            
            await inter.edit_original_response(
                content=f"✅ Відео готово, {user.mention}!\n**Промт:** `{prompt}`",
                file=disnake.File(out, filename="popka_video.mp4")
            )
        except Exception as e:
            try:
                await inter.edit_original_response(content=f"💥 Помилка: `{str(e)[:1500]}`")
            except Exception:
                pass
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
            task_queue.task_done()

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="Popka AI bot is running!"))
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

@bot.event
async def on_ready():
    global worker_started
    print(f"Popka AI запущен как {bot.user}!")
    if not worker_started:
        await start_web_server()
        bot.loop.create_task(process_queue())
        worker_started = True

@bot.slash_command(name="credits", description="Перевірити баланс кредитів")
async def credits_cmd(inter: disnake.ApplicationCommandInteraction):
    uid = inter.author.id
    current_credits = check_and_update_credits(uid)
    await inter.response.send_message(f"💳 Твій баланс кредитів: **{current_credits} / {MAX_CREDITS}**", ephemeral=True)

async def check_user_access(inter):
    uid = inter.author.id
    account_age_days = (datetime.now(timezone.utc) - inter.author.created_at).days
    if account_age_days < 7:
        await inter.response.send_message("⚠️ Акаунт занадто новий (потрібен вік від 7 днів).", ephemeral=True)
        return False
    current_credits = check_and_update_credits(uid)
    if current_credits <= 0:
        await inter.response.send_message("❌ Ліміт кредитів на сьогодні вичерпано (5/день).", ephemeral=True)
        return False
    user_credits[uid]["credits"] -= 1
    return True

@bot.slash_command(name="sd2", description="Згенерувати відео через Wan 2.1 (Hugging Face)")
async def sd2(inter: disnake.ApplicationCommandInteraction, prompt: str):
    if not await check_user_access(inter):
        return
    await inter.response.defer()
    await task_queue.put((inter, prompt))

bot.run(DISCORD_TOKEN)
          
