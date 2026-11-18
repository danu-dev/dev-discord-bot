# DEV Discord Bot (Groq AI + QA + Auto-Role)

Bot asisten manajemen server Discord dengan otak AI Groq (`openai/gpt-oss-120b`), sistem QA otomatis, Self-Role Picker, dan Join-to-Create Temp Voice.

## Deploy Gratis 24 Jam (Koyeb / Render)
1. Upload folder ini ke repository **GitHub** (boleh Private atau Public).
2. Buat akun gratis di **https://app.koyeb.com** (atau **https://render.com**).
3. Pilih **Create Service -> GitHub**, lalu pilih repo ini.
4. Set **Build command**: `pip install -r requirements.txt`
5. Set **Run command**: `python3 bot.py`
6. Tambahkan **Environment Variables**:
   - `DISCORD_TOKEN` = token bot Discord kamu
   - `GROQ_KEY` = API key Groq kamu (`gsk_...`)
7. Klik **Deploy**!
