import requests


def test():
    url = "https://apply.workable.com/payabl/j/129EF81C7C/"

    output = {'is_active': 'true', 'status_code': 200, 'reason': 'active_by_default'}

    if not url:
        return {'is_active': 'false', 'status_code': 0, 'reason': 'no_url'}

    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5'
        }

        # Збільшив таймаут до 15 сек
        response = requests.get(url, headers=headers, timeout=15, allow_redirects=True)

        # Виправляємо кодування (щоб "вже не приймає" читалося правильно)
        response.encoding = response.apparent_encoding

        # 1. ТЕХНІЧНА ПЕРЕВІРКА (Тільки явні помилки)
        if response.status_code in [404, 410]:
            return {'is_active': 'false', 'status_code': response.status_code, 'reason': '404_dead'}

        # Якщо LinkedIn/сайт блокує бота (999/403) -> Вважаємо активною
        if response.status_code in [999, 403, 429]:
            return {'is_active': 'true', 'status_code': response.status_code, 'reason': 'bot_block_safe_mode'}

        page_text = response.text.lower()

        # 2. ПЕРЕВІРКА НА "ЖИТТЯ" (Кнопки) - Це найголовніший пріоритет
        # Я розширив список, щоб він точно ловив Workable ("Apply for this job")
        active_markers = [
            "apply now", "apply for this", "apply on company site", "send application", "Apply", # EN
            "ansøg nu", "søg jobbet", "send ansøgning",  # DK
            "nộp đơn ngay", "ứng tuyển ngay",  # VN
            "подати заявку", "відгукнутися", "відгукнутись",  # UA
            "подать заявку", "откликнуться",  # RU
            "candidatar-se", "enviar candidatura",  # PT
            "jetzt bewerben", "online bewerben", "bewerbung",  # DE
            "solliciteer nu", "direct solliciteren",  # NL
            "candidati ora", "invia candidatura",  # IT
            "ansök nu", "sök tjänsten",  # SE
            "aplikuj teraz", "wyślij aplikację",  # PL
            "odpovědět", "přihlásit se",  # CZ
            "jelentkezés", "pályázat benyújtása",  # HU
            "aplică acum", "depune candidatura",  # RO
            "postuler maintenant", "candidater",  # FR
            "inscribirme", "enviar cv", "aplicar ahora",  # ES
            "submit application", "start application"  # Extra EN
        ]

        # 3. ПЕРЕВІРКА НА "СМЕРТЬ" (Текст закриття)
        closed_markers = [
            "no longer accepting applications", "job posting has expired", "position is closed",
            "modtager ikke længere ansøgninger",
            "không còn chấp nhận đơn đăng ký",
            "вже не приймає заявки",
            "não está mais aceitando candidaturas", "vaga encerrada",
            "nimmt keine bewerbungen mehr entgegen", "anzeige ist abgelaufen",
            "neemt geen sollicitaties meer aan", "vacature is gesloten",
            "non accetta più candidature", "offerta di lavoro scaduta",
            "tar inte längre emot ansökningar", "annonsen har löpt ut",
            "nie przyjmuje już aplikacji", "ogłoszenie wygasło",
            "již nepřijímá žádné žádosti", "pozice byla obsazena",
            "már nem fogad el jelentkezéseket", "hirdetés lejárt",
            "nu mai acceptă candidaturi", "anunț expirat",
            "n'accepte plus de candidatures",
            "ya no acepta solicitudes"
        ]

        has_apply_button = any(m in page_text for m in active_markers)
        is_closed_text_found = any(m in page_text for m in closed_markers)

        # --- ЛОГІКА ПРИЙНЯТТЯ РІШЕННЯ ---

        if has_apply_button:
            # Є кнопка -> Вакансія точно АКТИВНА (ігноруємо все інше)
            output.update({'is_active': 'true', 'reason': 'active_apply_button_found'})

        elif is_closed_text_found:
            # Кнопки немає, але є текст про закриття -> ЗАКРИТА
            output.update({'is_active': 'false', 'reason': 'text_closed_detected'})

        else:
            # Ні кнопок, ні тексту про закриття -> Лишаємо АКТИВНОЮ (безпечний режим)
            output.update({'is_active': 'true', 'reason': 'active_no_markers_found'})

    except Exception as e:
        output.update({'is_active': 'true', 'reason': f'error_safe_mode: {str(e)}'})

    return output


if __name__ == "__main__":
    print(test())