# StyleHub AIAI Chat — Laravel Integration Guide

## 1. Build the project

```bash
npm run build
```

This produces a `dist/` folder.

## 2. Copy files to Laravel

Copy the entire `dist/` folder into your Laravel project:

```
dist/  →  public/chatbox/
```

Your `public/chatbox/` should look like:

```
public/
  chatbox/
    index.html
    assets/
      index-xxxxx.js
      index-xxxxx.css
```

## 3. Add to your Blade layout

Open `resources/views/layouts/app.blade.php` (or whichever is your master layout).

Add these two lines **before the closing `</body>` tag**:

```html
<!-- StyleHub AIAI Chat Widget -->
<link  rel="stylesheet" href="/chatbox/assets/index-xxxxx.css">
<script src="/chatbox/assets/index-xxxxx.js" defer type="module"></script>
<enox-chat></enox-chat>
```

> Replace `index-xxxxx` with the actual hashed filenames from `public/chatbox/assets/`.
> To avoid updating the filename every rebuild, see step 4.

## 4. (Recommended) Fix asset filenames

To keep filenames stable between builds, add this to `vite.config.js`:

```js
build: {
  rollupOptions: {
    output: {
      entryFileNames: 'assets/chat.js',
      chunkFileNames: 'assets/chat-[hash].js',
      assetFileNames: 'assets/chat.[ext]',
    },
  },
},
```

Then your Blade tags become permanent:

```html
<link  rel="stylesheet" href="/chatbox/assets/chat.css">
<script src="/chatbox/assets/chat.js" defer type="module"></script>
<enox-chat></enox-chat>
```

## 5. CORS — FastAPI side

Make sure your FastAPI app allows your Laravel domain.
In `main.py`:

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://your-laravel-site.com", "http://localhost:8000"],
    allow_methods=["*"],
    allow_headers=["*"],
)
```

## 6. Change the API base URL for production

In `src/api/chat.js`, update:

```js
const BASE_URL = 'https://your-fastapi-domain.com/api/v1'
```

Then rebuild and re-copy to `public/chatbox/`.
