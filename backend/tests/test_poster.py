from PIL import Image, ImageStat

from app.generation.image_generator import ImageGenerator


def test_brand_poster_is_rendered_offline_and_not_blank(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIRO_IMAGE_TIER", "poster")
    title = "Small open models now beat last year's giants on code review"
    out = ImageGenerator().generate_image("x", str(tmp_path / "p.jpg"), topic="ai_breakthroughs", title=title)
    im = Image.open(out)
    assert im.size == (1200, 800)
    assert ImageStat.Stat(im.convert("L")).stddev[0] > 15  # не порожнє тло

    again = ImageGenerator().generate_image("x", str(tmp_path / "q.jpg"), topic="ai_breakthroughs", title=title)
    assert list(Image.open(again).getdata())[:2000] == list(im.getdata())[:2000]  # детермінований малюнок
