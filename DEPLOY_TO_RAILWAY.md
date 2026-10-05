# 🚀 How to Deploy Adbot to Railway (24/7 Hosting)

Your repository [`netflixidpkartik-dot/Adbot`](https://github.com/netflixidpkartik-dot/Adbot) is completely configured with `railway.json` and `Procfile`.

---

## Method 1: Web Dashboard (Quickest & Recommended)

### Step 1: Create Project on Railway
1. Go to [railway.com](https://railway.com) and log in with GitHub.
2. Click **+ New Project** ➔ **Deploy from GitHub repo**.
3. Select `netflixidpkartik-dot/Adbot`.
4. Click **Deploy Now**.

---

### Step 2: Add Environment Variables
1. Click on your deployed service in the Railway canvas.
2. Go to the **Variables** tab.
3. Click **RAW Editor** in the top-right of the variables tab.
4. Paste the following configuration (replace with your permanent bot tokens and MongoDB URI):

```env
API_ID=35773395
API_HASH=bbc39743d27f40b171ce2792808911b4
BOT_TOKEN=YOUR_MAIN_ADBOT_TOKEN
LOGGER_BOT_TOKEN=YOUR_LOGGER_BOT_TOKEN
ADMIN_ID=6849095840
MONGO_URI=YOUR_MONGODB_CONNECTION_STRING
PERMANENT_GROUP_FOLDER=https://t.me/addlist/Qt31BHYkGgk2MGFl
```

5. Click **Save** / **Deploy**.

---

## Method 2: Let Antigravity Deploy for You via Railway CLI

If you want me to deploy it directly from the terminal:
1. Generate an API token at [railway.com/account/tokens](https://railway.com/account/tokens).
2. Provide the token and your desired bot tokens / Mongo URI.
3. I will connect, create the Railway service, set the variables, and trigger the live build.
