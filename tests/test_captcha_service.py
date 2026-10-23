from pathlib import Path

from services.captcha_service import CaptchaService


def test_captcha_service_selects_known_answer_and_validates(tmp_path):
    assets_dir = tmp_path / "assets" / "captcha"
    assets_dir.mkdir(parents=True)
    for idx in range(1, 6):
        (assets_dir / f"{idx}.png").write_bytes(b"fake-png")

    answers_path = tmp_path / "data" / "captcha_answers.json"
    answers_path.parent.mkdir(parents=True)
    answers_path.write_text('{"1": "12345", "2": "23456", "3": "34567", "4": "45678", "5": "56789"}', encoding="utf-8")

    service = CaptchaService(asset_dir=assets_dir, answers_path=answers_path)

    challenge = service.get_challenge()
    assert challenge["image_file"] in {"1.png", "2.png", "3.png", "4.png", "5.png"}
    assert challenge["answer"] == service.get_answer_for(challenge["image_file"])
    assert service.validate(challenge["image_file"], challenge["answer"]) is True
    assert service.validate(challenge["image_file"], "wrong") is False


def test_captcha_preserves_leading_zero_and_requires_five_digits(tmp_path):
    assets_dir = tmp_path / "captcha"
    assets_dir.mkdir()
    (assets_dir / "1.png").write_bytes(b"image")
    answers_path = tmp_path / "answers.json"
    answers_path.write_text('{"1": "01234"}', encoding="utf-8")
    service = CaptchaService(assets_dir, answers_path)

    assert service.validate("1.png", "01234") is True
    assert service.validate("1.png", "1234") is False
    assert service.validate("1.png", " 01234") is False


def test_captcha_is_unavailable_when_image_or_answer_is_missing(tmp_path):
    assets_dir = tmp_path / "captcha"
    assets_dir.mkdir()
    answers_path = tmp_path / "answers.json"
    answers_path.write_text('{"1": "12345"}', encoding="utf-8")
    service = CaptchaService(assets_dir, answers_path)
    assert service.get_challenge() is None

    (assets_dir / "1.png").write_bytes(b"image")
    answers_path.write_text("{}", encoding="utf-8")
    assert service.get_challenge() is None
