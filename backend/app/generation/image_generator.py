import os
import re
import math
import logging
from typing import Optional
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
from app.core.config import settings

logger = logging.getLogger("kairo.image")


# Фірмовий візуальний стиль Kairo (світла тема): світлі, «повітряні»
# редакційні ілюстрації з світло-сірими тонами та синіми акцентами.
BRAND_LEAD = "Light airy editorial illustration: "

BRAND_STYLE = (
    ", bright editorial illustration, soft white, light grey and pale blue palette"
    " with muted navy and steel-blue accents, white background, natural daylight,"
    " clean minimal composition, gentle shadows, matte paper texture,"
    " no text, no lettering, no numbers, no captions, no watermark,"
    " no charts, no diagrams, no user interface, no screenshots, no neon"
)


class ImageGenerator:
    """
    Image Generator for Bonsai Image ternary 4B (GEMLite 2-bit).
    Provides high-speed online synthesis via Pollinations (Flux / SDXL),
    local diffusers inference if CUDA weights are present,
    or procedural high-contrast digital artwork fallback.
    """

    def __init__(self):
        self.model_name = settings.IMAGE_MODEL_NAME
        self.pipeline = None
        self._initialized = False

    def _init_diffusers(self):
        if self._initialized:
            return
        try:
            import torch
            if torch.cuda.is_available():
                from diffusers import DiffusionPipeline
                self.pipeline = DiffusionPipeline.from_pretrained(
                    self.model_name,
                    torch_dtype=torch.float16,
                ).to("cuda")
                logger.info("Bonsai Image model loaded successfully on CUDA.")
        except Exception as e:
            logger.info("Diffusers pipeline not initialized (using fallback generator): %s", e)
        finally:
            self._initialized = True

    def _prepare_english_prompt(self, prompt: str, topic: str = "") -> str:
        """
        Pollinations & diffusion models require English prompts.
        If prompt contains Cyrillic or is too brief, construct an evocative English prompt.
        """
        has_cyrillic = bool(re.search(r"[\u0400-\u04FF]", prompt))
        if has_cyrillic or len(prompt.strip()) < 10:
            topic_mapping = {
                "ai_breakthroughs": "An abstract neural network of thin golden lines and small ruby nodes on ivory paper",
                "developer_humor": "A cheerful software engineer at a bright desk by a sunny window, coffee mug and laptop",
                "open_source_tools": "Neatly arranged modular wooden building blocks and tools on a light workbench",
                "tech_career_tips": "A career staircase drawn in ruby ink on light drafting paper with small figures",
                "system_architecture": "Clean isometric server racks and connected modules on a light cream background",
            }
            english_topic = topic_mapping.get(topic, "")
            if not english_topic:
                ascii_words = re.findall(r"[A-Za-z0-9_]+", prompt)
                if ascii_words and len(" ".join(ascii_words)) > 8:
                    english_topic = " ".join(ascii_words)
                else:
                    english_topic = "Modern cloud architecture and artificial intelligence data mesh"
            return f"{BRAND_LEAD}{english_topic[:150]}{BRAND_STYLE}"

        clean = re.sub(r"\s+", " ", prompt).strip()
        return f"{BRAND_LEAD}{clean[:150]}{BRAND_STYLE}"

    def generate_image(
        self,
        prompt: str,
        output_path: str,
        width: int = 1200,
        height: int = 800,
        topic: str = "",
        title: str = "",
    ) -> str:
        import urllib.request
        import urllib.parse
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

        is_jpeg = output_path.lower().endswith((".jpg", ".jpeg"))
        target_format = "JPEG" if is_jpeg else "PNG"
        english_prompt = self._prepare_english_prompt(prompt, topic)

        import httpx

        # Офлайн-режим (демо без мережі): одразу фірмовий плакат.
        if os.environ.get("KAIRO_IMAGE_TIER", "").lower() == "poster":
            return self._render_brand_poster(output_path, target_format, width, height, topic, title)

        # 1. High-fidelity generative AI image (Pollinations)
        try:
            encoded_prompt = urllib.parse.quote(english_prompt[:420])
            url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width={width}&height={height}&nologo=true"
            logger.info("Tier 1: Requesting AI image from Pollinations: %s", english_prompt[:60])
            with httpx.Client(timeout=httpx.Timeout(connect=6.0, read=75.0, write=6.0, pool=6.0), follow_redirects=True) as client:
                resp = client.get(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
                if resp.status_code == 200 and len(resp.content) > 3000:
                    pil_img = Image.open(BytesIO(resp.content))
                    if pil_img.mode != "RGB":
                        pil_img = pil_img.convert("RGB")
                    pil_img.save(output_path, target_format, quality=92)
                    logger.info("Successfully generated AI image via Pollinations (%d bytes)", len(resp.content))
                    return output_path
        except Exception as e:
            logger.info("Tier 1 (Pollinations) skipped (%s), falling back to local generation...", e)

        # Стоковий тир свідомо прибрано: він віддавав випадкові фотографії з
        # чужими вотермарками, не пов'язані з темою матеріалу. Для роботи, яку
        # показують на захисті, охайний фірмовий плакат кращий за чужу картинку.

        # 2. Локальний diffusers-пайплайн, якщо він доступний у контейнері
        self._init_diffusers()
        if self.pipeline is not None:
            try:
                image = self.pipeline(prompt, num_inference_steps=4, width=width, height=height).images[0]
                if image.mode != "RGB":
                    image = image.convert("RGB")
                image.save(output_path, target_format, quality=92)
                return output_path
            except Exception as e:
                logger.warning("Diffusers generation failed: %s", e)

        # 3. Фірмовий плакат у сіро-синій палітрі.
        # Це остання лінія оборони, коли мережа недоступна: замість технічного
        # дампа промпта користувач отримує охайну типографічну обкладинку.
        logger.info("Tier 4: rendering branded Kairo poster")
        return self._render_brand_poster(output_path, target_format, width, height, topic, title)

    # ------------------------------------------------------------------
    # Фірмовий плакат-заглушка
    # ------------------------------------------------------------------
    @staticmethod
    def _font(name: str, size: int):
        candidates = {
            "serif": ["/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf", "C:/Windows/Fonts/georgiab.ttf"],
            "sans": ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "C:/Windows/Fonts/segoeui.ttf"],
            "sans-bold": ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "C:/Windows/Fonts/segoeuib.ttf"],
        }
        for path in candidates[name]:
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
        return ImageFont.load_default()

    def _render_brand_poster(
        self, output_path: str, target_format: str, width: int, height: int,
        topic: str, title: str,
    ) -> str:
        # Обкладинка-ілюстрація: темний сіро-синій градієнт, «нейромережа» з виділеним шляхом
        # (вибір параметрів), сяйво й частинки; малюнок детермінований для заданого заголовка.
        import random
        import zlib

        import numpy as np
        from PIL import ImageFilter

        W, H = width, height
        rng = random.Random(zlib.crc32(f"{topic}|{title}".encode("utf-8")))
        WHITE, SKY, ICE = (236, 243, 252), (143, 176, 217), (200, 222, 247)

        # Діагональний градієнт: глибокий синій → сталевий
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        t = np.clip(0.62 * xx / W + 0.38 * yy / H, 0, 1)[..., None]
        c0, c1 = np.array([12, 26, 48], np.float32), np.array([40, 78, 128], np.float32)
        img = Image.fromarray((c0 * (1 - t) + c1 * t).astype(np.uint8), "RGB").convert("RGBA")

        def layer():
            return Image.new("RGBA", (W, H), (0, 0, 0, 0))

        # М'які плями світла
        glow = layer()
        g = ImageDraw.Draw(glow)
        for cx, cy, r, col in ((0.74, 0.46, 0.34, (110, 160, 225, 120)), (0.18, 0.12, 0.30, (74, 111, 165, 110)),
                               (0.95, 0.95, 0.25, (143, 176, 217, 70))):
            g.ellipse([(W * cx - H * r, H * cy - H * r), (W * cx + H * r, H * cy + H * r)], fill=col)
        img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(H * 0.12)))

        # Концентричні кільця навколо мережі
        rings = layer()
        d = ImageDraw.Draw(rings)
        ncx, ncy = W * 0.765, H * 0.48
        for i in range(7):
            r = H * (0.16 + i * 0.075)
            d.ellipse([(ncx - r, ncy - r), (ncx + r, ncy + r)], outline=(143, 176, 217, 34 - i * 3), width=2)
        img.alpha_composite(rings)

        # Нейромережа: шари вузлів, ребра, один підсвічений шлях
        sizes = [3, 5, 6, 5, 3]
        x0, x1 = W * 0.59, W * 0.94
        nodes = []
        for li, n in enumerate(sizes):
            x = x0 + (x1 - x0) * li / (len(sizes) - 1)
            span = H * (0.08 + 0.095 * n)
            nodes.append([(x + rng.uniform(-6, 6), ncy - span / 2 + span * (k + 0.5) / n + rng.uniform(-8, 8))
                          for k in range(n)])
        # плавний шлях: наступний вузол поруч із пропорційною позицією попереднього
        path = [rng.randrange(sizes[0])]
        for a, b in zip(sizes, sizes[1:]):
            k = round(path[-1] * (b - 1) / (a - 1)) + rng.choice((-1, 0, 1))
            path.append(min(b - 1, max(0, k)))

        edges = layer()
        d = ImageDraw.Draw(edges)
        for li in range(len(sizes) - 1):
            for a in nodes[li]:
                for b in nodes[li + 1]:
                    d.line([a, b], fill=(143, 176, 217, rng.randint(22, 70)), width=1)
        img.alpha_composite(edges)

        hl = layer()
        d = ImageDraw.Draw(hl)
        pts = [nodes[li][k] for li, k in enumerate(path)]
        d.line(pts, fill=(200, 222, 247, 255), width=max(3, H // 220), joint="curve")
        img.alpha_composite(hl.filter(ImageFilter.GaussianBlur(H * 0.012)))
        img.alpha_composite(hl)

        halo, dots = layer(), layer()
        dh, dd = ImageDraw.Draw(halo), ImageDraw.Draw(dots)
        rn = H * 0.017
        for li, layer_nodes in enumerate(nodes):
            for k, (x, y) in enumerate(layer_nodes):
                on = k == path[li]
                rr = rn * (1.35 if on else 1.0)
                dh.ellipse([(x - rr * 2.6, y - rr * 2.6), (x + rr * 2.6, y + rr * 2.6)],
                           fill=(200, 222, 247, 150 if on else 60))
                dd.ellipse([(x - rr, y - rr), (x + rr, y + rr)], fill=(30, 58, 95, 255),
                           outline=ICE + (255,), width=2)
                ri = rr * (0.55 if on else 0.38)
                dd.ellipse([(x - ri, y - ri), (x + ri, y + ri)], fill=WHITE + (255,) if on else SKY + (255,))
        img.alpha_composite(halo.filter(ImageFilter.GaussianBlur(H * 0.02)))
        img.alpha_composite(dots)

        # Частинки, густіші праворуч
        dust = layer()
        d = ImageDraw.Draw(dust)
        for _ in range(170):
            x = W * (1 - rng.random() ** 1.7)
            y = rng.uniform(0, H)
            r = rng.choice((1, 1, 1.5, 2, 2.5))
            d.ellipse([(x - r, y - r), (x + r, y + r)], fill=(220, 233, 250, rng.randint(40, 170)))
        img.alpha_composite(dust)

        # Затемнення ліворуч під текст
        shade = np.clip(1 - xx[0] / (W * 0.62), 0, 1) ** 1.4 * 150
        mask = Image.fromarray(np.repeat(shade[None, :], H, axis=0).astype(np.uint8), "L")
        dark = Image.new("RGBA", (W, H), (8, 18, 34, 255))
        dark.putalpha(mask)
        img.alpha_composite(dark)

        draw = ImageDraw.Draw(img, "RGBA")
        pad = int(min(W, H) * 0.045)
        draw.rectangle([(pad, pad), (W - pad, H - pad)], outline=(143, 176, 217, 60), width=1)
        lx = pad + int(pad * 0.9)

        # Логотип із розрідженням літер і тема
        f_mark = self._font("sans-bold", max(16, int(H * 0.030)))
        x, y = lx, pad + int(pad * 0.8)
        for ch in "KAIRO":
            draw.text((x, y), ch, font=f_mark, fill=WHITE)
            x += draw.textlength(ch, font=f_mark) + max(3, int(H * 0.008))
        label = (topic or "kairo").replace("_", " ").upper()
        f_label = self._font("sans", max(12, int(H * 0.022)))
        draw.text((lx, y + int(H * 0.056)), label, font=f_label, fill=SKY)

        # Заголовок великим шрифтом у лівій половині
        f_title = self._font("serif", max(24, int(H * 0.058)))
        max_w = int(W * 0.45)
        lines, cur = [], ""
        for w in (title or "Generated by Kairo").split():
            probe = f"{cur} {w}".strip()
            if draw.textlength(probe, font=f_title) <= max_w or not cur:
                cur = probe
            else:
                lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        lines = lines[:5]
        line_h = int(H * 0.074)
        foot_y = H - pad - int(pad * 1.1) - int(H * 0.02)
        ty = foot_y - int(H * 0.07) - line_h * len(lines)
        draw.rectangle([(lx, ty - int(H * 0.045)), (lx + int(W * 0.05), ty - int(H * 0.045) + 3)], fill=SKY)
        for line in lines:
            draw.text((lx, ty), line, font=f_title, fill=WHITE)
            ty += line_h

        f_foot = self._font("sans", max(11, int(H * 0.019)))
        draw.text((lx, foot_y), "illustration rendered locally", font=f_foot, fill=(143, 176, 217, 200))

        img.convert("RGB").save(output_path, target_format, quality=94)
        return output_path
