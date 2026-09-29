from contract_search import ContractSearchParams
from kladr_service import KladrService
from zakupki_client import ZakupkiClient


def main():
    single = ContractSearchParams(
        search_string="Вориконазол",
        date_from="01.09.2025",
        date_to="30.09.2026",
        records_per_page=10,
        drug_znvlp=True,
        customer_places=["05000000000"],
    )

    multiple = ContractSearchParams(
        search_string="Вориконазол",
        date_from="01.09.2025",
        date_to="30.09.2026",
        records_per_page=10,
        drug_znvlp=True,
        customer_places=[
            "05000000000",
            "26000000000",
        ],
    )

    print("Один регион:")
    print(single.to_query_params(1))
    print()

    print("Два региона:")
    print(multiple.to_query_params(1))
    print()

    with ZakupkiClient(verify_ssl=False) as client:
        service = KladrService(client)

        root = service.get_root()
        print("Федеральных округов:", len(root))

        for item in root:
            print(
                item["code"],
                "|",
                item["name"],
                "| folder=",
                item["isFolder"],
            )

        print()
        children = service.get_children("OKER38")
        print("Регионы OKER38:", len(children))

        for item in children:
            print(item["code"], "|", item["name"])


if __name__ == "__main__":
    main()
