from zakupki_client import ZakupkiClient


def main():
    with ZakupkiClient(
        verify_ssl=False
    ) as client:
        print("Параметры защиты:")
        print("block_retries =", client.block_retries)
        print(
            "min_request_interval =",
            client.min_request_interval,
            "сек",
        )
        print(
            "proactive_pause_every =",
            client.proactive_pause_every,
            "запросов",
        )
        print(
            "proactive_pause_seconds =",
            client.proactive_pause_seconds,
            "сек",
        )

    print()
    print("Логика stage16:")
    print("403/429 -> весь pipeline ждёт")
    print("-> повторяется тот же запрос")
    print("-> после 200 работа продолжается")
    print(
        "-> если блокировка не снята после длинных пауз, "
        "весь запуск останавливается один раз"
    )


if __name__ == "__main__":
    main()
