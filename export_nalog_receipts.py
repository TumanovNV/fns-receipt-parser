#!/usr/bin/env python3
# Export receipts from ФНС "Мои чеки онлайн" (lkdr.nalog.ru).
# The Bearer token is entered locally and is never written to disk.

import csv
import getpass
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://lkdr.nalog.ru/api"
LIST_URL = f"{BASE}/v1/receipt"
DETAIL_URL = f"{BASE}/v1/receipt/fiscal_data"

PAGE_SIZE = 10          # exact page size observed in the web app
REQUEST_DELAY = 0.08    # gentle delay between API calls
TIMEOUT = 30

OUT_DIR = Path("nalog_receipts_export")
RAW_JSON = OUT_DIR / "receipts_full.json"
SUMMARY_CSV = OUT_DIR / "receipts_summary.csv"
ITEMS_CSV = OUT_DIR / "receipt_items.csv"
ERRORS_JSON = OUT_DIR / "errors.json"

def request_json(url, token, payload):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json;charset=UTF-8",
            "Accept": "application/json",
            "Origin": "https://lkdr.nalog.ru",
            "Referer": "https://lkdr.nalog.ru/",
            "User-Agent": "Mozilla/5.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code}: {body[:500]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Network error: {e}")

def normalize_token(raw):
    raw = raw.strip()
    if raw.lower().startswith("bearer "):
        raw = raw[7:].strip()
    return raw

def main():
    print("ФНС «Мои чеки онлайн» — локальный экспорт")
    print("Токен вводится только в терминале и НЕ сохраняется в файлы.")
    token = normalize_token(getpass.getpass("Вставьте Bearer token: "))
    if not token:
        print("Пустой token.", file=sys.stderr)
        sys.exit(2)

    OUT_DIR.mkdir(exist_ok=True)

    receipts = []
    offset = 0

    print("\n1/3 Получаю список чеков...")
    while True:
        payload = {
            "limit": PAGE_SIZE,
            "offset": offset,
            "dateFrom": None,
            "dateTo": None,
            "orderBy": "CREATED_DATE:DESC",
            "inn": None,
            "kktOwner": "",
        }
        response = request_json(LIST_URL, token, payload)
        page = response.get("receipts", [])
        if not isinstance(page, list):
            raise RuntimeError("Неожиданный ответ /v1/receipt: поле receipts не является списком")

        receipts.extend(page)
        print(f"  получено: {len(receipts)}", end="\r", flush=True)

        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
        time.sleep(REQUEST_DELAY)

    print(f"\nНайдено чеков: {len(receipts)}")

    print("\n2/3 Получаю состав каждого чека...")
    enriched = []
    errors = []

    for i, receipt in enumerate(receipts, 1):
        key = receipt.get("key")
        detail = None
        error = None

        if key:
            try:
                detail = request_json(DETAIL_URL, token, {"key": key})
            except Exception as e:
                error = str(e)
                errors.append({"key": key, "error": error})
            time.sleep(REQUEST_DELAY)
        else:
            error = "missing key"
            errors.append({"key": None, "receipt": receipt, "error": error})

        enriched.append({
            "receipt": receipt,
            "fiscal_data": detail,
            "error": error,
        })
        print(f"  обработано: {i}/{len(receipts)}", end="\r", flush=True)

    print()

    with RAW_JSON.open("w", encoding="utf-8") as f:
        json.dump(enriched, f, ensure_ascii=False, indent=2)

    if errors:
        with ERRORS_JSON.open("w", encoding="utf-8") as f:
            json.dump(errors, f, ensure_ascii=False, indent=2)

    print("\n3/3 Формирую CSV...")

    summary_fields = [
        "key",
        "createdDate",
        "receiveDate",
        "receiptDateTime",
        "seller",
        "sellerInn",
        "totalSum",
        "fiscalDocumentNumber",
        "fiscalDriveNumber",
        "sourceCode",
        "receiptState",
        "brandId",
        "itemsCount",
        "detailStatus",
    ]

    with SUMMARY_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=summary_fields, delimiter=";")
        w.writeheader()

        for row in enriched:
            r = row["receipt"] or {}
            d = row["fiscal_data"] or {}
            items = d.get("items") or []

            w.writerow({
                "key": r.get("key"),
                "createdDate": r.get("createdDate"),
                "receiveDate": r.get("receiveDate"),
                "receiptDateTime": d.get("dateTime"),
                "seller": d.get("user") or r.get("kktOwner"),
                "sellerInn": d.get("userInn") or r.get("kktOwnerInn"),
                "totalSum": d.get("totalSum", r.get("totalSum")),
                "fiscalDocumentNumber": r.get("fiscalDocumentNumber"),
                "fiscalDriveNumber": r.get("fiscalDriveNumber"),
                "sourceCode": r.get("sourceCode"),
                "receiptState": r.get("receiptState"),
                "brandId": r.get("brandId"),
                "itemsCount": len(items) if isinstance(items, list) else "",
                "detailStatus": "OK" if row["fiscal_data"] is not None else row["error"],
            })

    item_fields = [
        "receiptKey",
        "receiptDateTime",
        "seller",
        "sellerInn",
        "receiptTotalSum",
        "itemIndex",
        "name",
        "price",
        "quantity",
        "sum",
        "providerInn",
        "paymentType",
        "paymentAgentByProductType",
        "productType",
        "nds",
        "ndsSum",
        "labelCodeProcessMode",
        "itemsQuantityMeasure",
    ]

    with ITEMS_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=item_fields, delimiter=";")
        w.writeheader()

        for row in enriched:
            r = row["receipt"] or {}
            d = row["fiscal_data"] or {}
            items = d.get("items") or []
            if not isinstance(items, list):
                continue

            for idx, item in enumerate(items, 1):
                w.writerow({
                    "receiptKey": r.get("key"),
                    "receiptDateTime": d.get("dateTime"),
                    "seller": d.get("user") or r.get("kktOwner"),
                    "sellerInn": d.get("userInn") or r.get("kktOwnerInn"),
                    "receiptTotalSum": d.get("totalSum", r.get("totalSum")),
                    "itemIndex": idx,
                    "name": item.get("name"),
                    "price": item.get("price"),
                    "quantity": item.get("quantity"),
                    "sum": item.get("sum"),
                    "providerInn": item.get("providerInn"),
                    "paymentType": item.get("paymentType"),
                    "paymentAgentByProductType": item.get("paymentAgentByProductType"),
                    "productType": item.get("productType"),
                    "nds": item.get("nds"),
                    "ndsSum": item.get("ndsSum"),
                    "labelCodeProcessMode": item.get("labelCodeProcessMode"),
                    "itemsQuantityMeasure": item.get("itemsQuantityMeasure"),
                })

    print("\nГотово.")
    print(f"  Полный JSON: {RAW_JSON}")
    print(f"  Сводка чеков: {SUMMARY_CSV}")
    print(f"  Позиции чеков: {ITEMS_CSV}")
    if errors:
        print(f"  Ошибки отдельных чеков: {ERRORS_JSON}")
        print(f"  Не удалось получить детализацию: {len(errors)}")
    else:
        print("  Все чеки выгружены с детализацией.")

if __name__ == "__main__":
    main()
