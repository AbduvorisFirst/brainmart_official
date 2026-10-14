# -*- coding: utf-8 -*-
"""
update_and_compile_translations.py
Loads existing PO files, adds comprehensive dashboard translations for Uzbek and English,
updates the PO files, and compiles them into valid MO binary files for Django.
"""

import os
import re
import struct
import io
import gettext
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
LOCALE_DIR = BASE_DIR / "locale"

# Complete dictionary of dashboard translations
TRANSLATIONS = {
    # ─── 1. ФИЛИАЛЫ И ТОЧКИ (BRANCHES) ───
    "Точки и филиалы магазина": {
        "uz": "Do'kon nuqtalari va filiallari",
        "en": "Store Locations & Branches",
    },
    "Клиенты выбирают удобный филиал и вызывают такси к вашей точке.": {
        "uz": "Mijozlar qulay filialni tanlaydilar va taksini o'sha nuqtaga chaqiradilar.",
        "en": "Customers choose a convenient branch and hail a taxi to your store location.",
    },
    "Добавить филиал": {
        "uz": "Filial qo'shish",
        "en": "Add Branch",
    },
    "Тариф BrainMart SaaS:": {
        "uz": "BrainMart SaaS tarifi:",
        "en": "BrainMart SaaS Plan:",
    },
    "0% комиссии": {
        "uz": "0% komissiya",
        "en": "0% commission",
    },
    "450 000 сум/мес": {
        "uz": "450 000 so'm/oy",
        "en": "450,000 UZS/mo",
    },
    "сум/мес": {
        "uz": "so'm/oy",
        "en": "UZS/mo",
    },
    "вкл. 1-ю точку": {
        "uz": "1-nuqta kiritilgan",
        "en": "includes 1st location",
    },
    "доп. точки": {
        "uz": "qo'shimcha nuqtalar",
        "en": "extra locations",
    },
    "доп. филиалы: +150 000 сум/мес за каждый": {
        "uz": "qo'shimcha filiallar: har biri uchun +150 000 so'm/oy",
        "en": "extra branches: +150,000 UZS/mo each",
    },
    "Итого в месяц": {
        "uz": "Jami oylik to'lov",
        "en": "Monthly total",
    },
    "точек": {
        "uz": "nuqta",
        "en": "locations",
    },
    "Список ваших точек": {
        "uz": "Sizning nuqtalaringiz ro'yxati",
        "en": "List of Your Locations",
    },
    "Главная точка": {
        "uz": "Asosiy nuqta",
        "en": "Main location",
    },
    "Точка на карте:": {
        "uz": "Xaritadagi nuqta:",
        "en": "Map location:",
    },
    "Точка ещё не указана на карте": {
        "uz": "Nuqta hali xaritada belgilanmagan",
        "en": "Location not specified on map yet",
    },
    "Яндекс": {
        "uz": "Yandex",
        "en": "Yandex Maps",
    },
    "Изменить точку на карте": {
        "uz": "Xaritadagi nuqtani o'zgartirish",
        "en": "Edit Map Location",
    },
    "Вы действительно хотите удалить эту точку?": {
        "uz": "Haqiqatan ham ushbu nuqtani o'chirmoqchimisiz?",
        "en": "Are you sure you want to delete this location?",
    },
    "Назначить локацию на карте": {
        "uz": "Xaritada manzilni belgilash",
        "en": "Set Location on Map",
    },
    "Точка:": {
        "uz": "Nuqta:",
        "en": "Location:",
    },
    "Добавить новый филиал на карту": {
        "uz": "Xaritaga yangi filial qo'shish",
        "en": "Add New Branch on Map",
    },
    "Новый филиал": {
        "uz": "Yangi filial",
        "en": "New Branch",
    },
    "СОХРАНИТЬ ИЗМЕНЕНИЯ": {
        "uz": "O'ZGARISHLARNI SAQLASH",
        "en": "SAVE CHANGES",
    },
    "ДОБАВИТЬ ТОЧКУ": {
        "uz": "NUQTA QO'SHISH",
        "en": "ADD LOCATION",
    },
    "СОХРАНИТЬ ТОЧКУ": {
        "uz": "NUQTANI SAQLASH",
        "en": "SAVE LOCATION",
    },
    "Отмена": {
        "uz": "Bekor qilish",
        "en": "Cancel",
    },
    "🔍 Поиск адреса (напр: Чорсу, ЦУМ, Юнусабад...)": {
        "uz": "🔍 Manzil qidirish (masalan: Chorsu, TSUM, Yunusobod...)",
        "en": "🔍 Search address (e.g. Chorsu, TSUM, Yunusabad...)",
    },
    "Найти": {
        "uz": "Topish",
        "en": "Search",
    },
    "Определить моё местоположение": {
        "uz": "Mening joylashuvimni aniqlash",
        "en": "Detect my location",
    },
    "Кликните на карту в нужном месте или перетащите маркер, чтобы выставить точку.": {
        "uz": "Nuqtani belgilash uchun xaritadagi kerakli joyni bosing yoki markerni suring.",
        "en": "Click anywhere on the map or drag the marker to set the location pin.",
    },
    "Название точки": {
        "uz": "Nuqta nomi",
        "en": "Location Name",
    },
    "Адрес точки": {
        "uz": "Nuqta manzili",
        "en": "Location Address",
    },
    "Широта (Lat)": {
        "uz": "Kenglik (Lat)",
        "en": "Latitude (Lat)",
    },
    "Долгота (Lon)": {
        "uz": "Uzunlik (Lon)",
        "en": "Longitude (Lon)",
    },
    "Широта": {
        "uz": "Kenglik",
        "en": "Latitude",
    },
    "Долгота": {
        "uz": "Uzunlik",
        "en": "Longitude",
    },
    "Управление точками и филиалами": {
        "uz": "Filiallar va savdo nuqtalarini boshqarish",
        "en": "Store Branches & Locations Management",
    },

    # ─── 2. ФИНАНСЫ (FINANCE) ───
    "Финансы": {
        "uz": "Moliya",
        "en": "Finance",
    },
    "SaaS-подписка": {
        "uz": "SaaS-obuna",
        "en": "SaaS Subscription",
    },
    "комиссии с продаж": {
        "uz": "savdolardan komissiya",
        "en": "sales commission",
    },
    "Как клиенты платят за заказы": {
        "uz": "Mijozlar buyurtmalar uchun qanday to'laydilar",
        "en": "How Customers Pay for Orders",
    },
    "Простая и прозрачная схема без посредников": {
        "uz": "Vositasiz oddiy va shaffof tizim",
        "en": "Simple and transparent flow with zero intermediaries",
    },
    "Клиент переводит напрямую": {
        "uz": "Mijoz to'g'ridan-to'g'ri o'tkazadi",
        "en": "Customer transfers directly",
    },
    "Клиент переводит деньги прямо на вашу карту Uzcard/Humo": {
        "uz": "Mijoz pulni to'g'ridan-to'g'ri sizning Uzcard/Humo kartangizga o'tkazadi",
        "en": "Customer transfers funds directly to your Uzcard/Humo card",
    },
    "Вы получаете 100%": {
        "uz": "Siz 100% olasiz",
        "en": "You receive 100%",
    },
    "Деньги сразу на вашей карте. Никаких удержаний платформой": {
        "uz": "Pul darhol kartangizda. Platforma tomonidan hech qanday ushlab qolishlar yo'q",
        "en": "Money arrives directly on your card. Zero platform deductions",
    },
    "Клиент присылает скрин": {
        "uz": "Mijoz chek skrinshotini yuboradi",
        "en": "Customer sends screenshot",
    },
    "Клиент прикладывает подтверждение перевода — вы подтверждаете заказ": {
        "uz": "Mijoz to'lov chekini biriktiradi — siz buyurtmani tasdiqlaysiz",
        "en": "Customer attaches payment proof — you confirm the order",
    },
    "Ваша карта для приёма оплат": {
        "uz": "To'lovlarni qabul qilish uchun kartangiz",
        "en": "Your Payment Receiving Card",
    },
    "Скопировать": {
        "uz": "Nusxa olish",
        "en": "Copy",
    },
    "Покажите эту карту клиентам для оплаты заказов. После получения перевода — подтвердите заказ в разделе «Заказы».": {
        "uz": "Buyurtmalarga to'lov uchun ushbu kartani mijozlarga ko'rsating. Pul tushgach — «Buyurtmalar» bo'limida tasdiqlang.",
        "en": "Show this card to customers for order payments. Once payment is received, confirm it in Orders.",
    },
    "Карта не привязана!": {
        "uz": "Karta ulanmagan!",
        "en": "Card not linked!",
    },
    "Добавьте номер карты ниже, чтобы клиенты знали, куда переводить деньги за заказы.": {
        "uz": "Mijozlar buyurtmalar uchun pul o'tkaza olishi uchun pastda karta raqamini kiriting.",
        "en": "Add your card number below so customers know where to transfer money for orders.",
    },
    "Карта для приёма оплат от клиентов": {
        "uz": "Mijozlardan to'lov qabul qilish uchun karta",
        "en": "Card for receiving payments from customers",
    },
    "Эта карта будет показана клиентам при оформлении заказа. Клиент переводит деньги напрямую на неё.": {
        "uz": "Ushbu karta buyurtma berishda mijozlarga ko'rsatiladi. Mijoz to'g'ridan-to'g'ri unga pul o'tkazadi.",
        "en": "This card will be displayed to customers at checkout. Customers transfer money directly to it.",
    },
    "Номер карты (16 цифр)": {
        "uz": "Karta raqami (16 ta raqam)",
        "en": "Card number (16 digits)",
    },
    "Владелец карты (ФИО)": {
        "uz": "Karta egasi (F.I.SH)",
        "en": "Card holder (Full Name)",
    },
    "Сохранить карту": {
        "uz": "Kartani saqlash",
        "en": "Save Card",
    },
    "Ежемесячная подписка BrainMart": {
        "uz": "Oylik BrainMart obunasi",
        "en": "Monthly BrainMart Subscription",
    },
    "Оплачено до:": {
        "uz": "To'langan muddat:",
        "en": "Paid until:",
    },
    "Оплатить переводом": {
        "uz": "O'tkazma orqali to'lash",
        "en": "Pay via Card Transfer",
    },
    "Точки:": {
        "uz": "Nuqtalar:",
        "en": "Locations:",
    },
    "Первая точка: 450 000 сум · Каждая дополнительная: +150 000 сум/мес": {
        "uz": "Birinchi nuqta: 450 000 so'm · Har bir qo'shimcha: +150 000 so'm/oy",
        "en": "First location: 450,000 UZS · Each extra: +150,000 UZS/month",
    },
    "Управление точками": {
        "uz": "Nuqtalarni boshqarish",
        "en": "Manage Locations",
    },
    "Подписка покрывает работу CRM, ИИ-продавца, витрины и Telegram Mini App. При неуплате действует льготный период 5 дней.": {
        "uz": "Obuna CRM, AI-sotuvchi, onlayn-vitrina va Telegram Mini App xizmatlarini qamrab oladi. To'lov qilinmasa 5 kun imtiyozli davr beriladi.",
        "en": "Subscription covers CRM, AI Seller, Web Storefront and Telegram Mini App. 5 days grace period on non-payment.",
    },
    "История оплат подписки": {
        "uz": "Obuna to'lovlari tarixi",
        "en": "Subscription Payment History",
    },
    "до": {
        "uz": "gacha",
        "en": "until",
    },
    "Перевод на карту": {
        "uz": "Kartaga o'tkazma",
        "en": "Card transfer",
    },
    "Детализация оплаченных заказов": {
        "uz": "To'langan buyurtmalar tafsilotlari",
        "en": "Paid Orders Details",
    },
    "Пока нет оплаченных заказов.": {
        "uz": "Hozircha to'langan buyurtmalar yo'q.",
        "en": "No paid orders yet.",
    },
    "Оплата подписки BrainMart": {
        "uz": "BrainMart obunasini to'lash",
        "en": "BrainMart Subscription Payment",
    },
    "Переведите деньги на карту BrainMart": {
        "uz": "BrainMart kartasiga pul o'tkazing",
        "en": "Transfer funds to BrainMart card",
    },
    "Получатель": {
        "uz": "Qabul qiluvchi",
        "en": "Recipient",
    },
    "Выберите срок продления": {
        "uz": "Muddatni tanlang",
        "en": "Choose renewal period",
    },
    "мес.": {
        "uz": "oy",
        "en": "mo.",
    },
    "Сумма к переводу": {
        "uz": "O'tkaziladigan summa",
        "en": "Amount to transfer",
    },
    "Номер чека или последние 4 цифры карты": {
        "uz": "Chek raqami yoki kartaning oxirgi 4 raqami",
        "en": "Receipt number or last 4 digits of card",
    },
    "Укажите номер транзакции или карту, с которой был сделан перевод. Подписка активируется после проверки.": {
        "uz": "Tranzaksiya raqami yoki to'lov qilingan kartani ko'rsating. Obuna tekshiruvdan so'ng faollashtiriladi.",
        "en": "Specify the transaction number or the card used for the transfer. Subscription activates upon verification.",
    },
    "Я перевёл, активировать": {
        "uz": "O'tkazdim, faollashtirish",
        "en": "I transferred, activate",
    },
    "Подписка покрывает работу CRM, ИИ-продавца, витрины и Telegram Mini App. При неуплате действует льготный период 6 дней.": {
        "uz": "Obuna CRM, AI-sotuvchi, vitrina va Telegram Mini App faoliyatini qoplaydi. To'lov qilinmaganda 6 kunlik imtiyozli davr amal qiladi.",
        "en": "The subscription covers CRM, AI sales assistant, storefront, and Telegram Mini App. A 6-day grace period applies if unpaid.",
    },
    "На проверке": {
        "uz": "Tekshiruvda",
        "en": "Under review",
    },
    "Подтверждена": {
        "uz": "Tasdiqlangan",
        "en": "Approved",
    },
    "Отклонена": {
        "uz": "Rad etilgan",
        "en": "Rejected",
    },
    "Смотреть чек": {
        "uz": "Chekni ko'rish",
        "en": "View receipt",
    },
    "Скан / Фото чека перевода": {
        "uz": "O'tkazma chekining fotosurati / skani",
        "en": "Transfer receipt scan / photo",
    },
    "Нажмите, чтобы прикрепить фото или скан чека": {
        "uz": "Chek fotosurati yoki skanini yuklash uchun bosing",
        "en": "Click to attach photo or receipt scan",
    },
    "Скриншот или квитанция из банковского приложения": {
        "uz": "Bank ilovasidagi skrinshot yoki kvitansiya",
        "en": "Screenshot or receipt from bank app",
    },
    "Номер транзакции или комментарий (необязательно)": {
        "uz": "Tranzaksiya raqami yoki izoh (ixtiyoriy)",
        "en": "Transaction ID or comment (optional)",
    },
    "После отправки чек мгновенно поступает администратору. Подписка продлевается сразу после подтверждения.": {
        "uz": "Yuborilgandan so'ng chek darhol administratorga boradi. Obuna tasdiqlangach darhol uzaytiriladi.",
        "en": "After sending, the receipt is instantly delivered to the administrator. The subscription is renewed once approved.",
    },
    "Я перевёл, отправить чек": {
        "uz": "O'tkazdim, chekni yuborish",
        "en": "I transferred, send receipt",
    },
    "Чек прикреплен": {
        "uz": "Chek biriktirildi",
        "en": "Receipt attached",
    },
    "Нажмите, чтобы заменить файл": {
        "uz": "Faylni almashtirish uchun bosing",
        "en": "Click to change file",
    },
    "Заказ": {
        "uz": "Buyurtma",
        "en": "Order",
    },
    "Сумма": {
        "uz": "Summa",
        "en": "Amount",
    },
    "Метод оплаты": {
        "uz": "To'lov usuli",
        "en": "Payment method",
    },
    "Дата": {
        "uz": "Sana",
        "en": "Date",
    },
    "Прямой перевод на карту продавца": {
        "uz": "Sotuvchi kartasiga to'g'ridan-to'g'ri o'tkazma",
        "en": "Direct transfer to seller card",
    },

    # ─── 3. ГЛАВНАЯ (HOME) ───
    "Ваш магазин на модерации. Товары появятся на витрине после проверки.": {
        "uz": "Do'koningiz tekshiruvda. Tovarlar tasdiqlangandan so'ng vitrinada ko'rinadi.",
        "en": "Your store is under moderation. Products will appear on the storefront after review.",
    },
    "Мой бренд": {
        "uz": "Mening brendim",
        "en": "My Brand",
    },
    "Логотип виден клиентам в вашем каталоге.": {
        "uz": "Logotip mijozlarga katalogingizda ko'rinadi.",
        "en": "The logo is visible to customers in your catalog.",
    },
    "Отзывы клиентов": {
        "uz": "Mijozlar fikrlari",
        "en": "Customer Reviews",
    },
    "Выручка": {
        "uz": "Tushum",
        "en": "Revenue",
    },
    "Оплачено": {
        "uz": "To'langan",
        "en": "Paid",
    },
    "В ожидании": {
        "uz": "Kutilmoqda",
        "en": "Pending",
    },
    "В каталоге": {
        "uz": "Katalogda",
        "en": "In Catalog",
    },
    "сум": {
        "uz": "so'm",
        "en": "UZS",
    },
    "ИИ-Советник по росту продаж (Gemini AI)": {
        "uz": "Savdoni oshirish bo'yicha AI maslahatchi (Gemini AI)",
        "en": "Sales Growth AI Advisor (Gemini AI)",
    },
    "Аналитика ИИ-Продавца": {
        "uz": "AI-Sotuvchi tahlili",
        "en": "AI Seller Analytics",
    },
    "Диалогов с клиентами:": {
        "uz": "Mijozlar bilan suhbatlar:",
        "en": "Customer conversations:",
    },
    "диалогов": {
        "uz": "suhbat",
        "en": "chats",
    },
    "Ответов от ИИ-агента:": {
        "uz": "AI javoblari soni:",
        "en": "AI replies sent:",
    },
    "Конверсия в заказ:": {
        "uz": "Buyurtmaga aylanish (konversiya):",
        "en": "Conversion rate:",
    },
    "Gemini автоматически подбирает товары по описанию и фото ваших клиентов.": {
        "uz": "Gemini mijozlaringiz tavsifi va rasmlariga qarab tovarlarni avtomatik tanlaydi.",
        "en": "Gemini automatically matches products based on customer descriptions and photos.",
    },
    "Продажи за 7 дней": {
        "uz": "So'nggi 7 kunlik savdo",
        "en": "Sales for the last 7 days",
    },
    "Заканчиваются на складе": {
        "uz": "Omborda tugab qolmoqda",
        "en": "Running low in stock",
    },
    "Товар": {
        "uz": "Tovar",
        "en": "Product",
    },
    "Цвет/Размер": {
        "uz": "Rang/O'lcham",
        "en": "Color/Size",
    },
    "Остаток": {
        "uz": "Qoldiq",
        "en": "Stock",
    },
    "шт": {
        "uz": "dona",
        "en": "pcs",
    },
    "Возврат спящих клиентов": {
        "uz": "Nofaol mijozlarni qaytarish",
        "en": "Re-engaging inactive customers",
    },
    "Создать рассылку": {
        "uz": "Xabar yuborish",
        "en": "Create broadcast",
    },

    # ─── 4. ТОВАРЫ (PRODUCTS & ADD PRODUCT) ───
    "Мои товары": {
        "uz": "Mening tovarlarim",
        "en": "My Products",
    },
    "Активен": {
        "uz": "Faol",
        "en": "Active",
    },
    "Скрыт": {
        "uz": "Yashirilgan",
        "en": "Hidden",
    },
    "Простой товар": {
        "uz": "Oddiy tovar",
        "en": "Simple product",
    },
    "шт.": {
        "uz": "dona",
        "en": "pcs",
    },
    "Скрыть": {
        "uz": "Yashirish",
        "en": "Hide",
    },
    "Показать": {
        "uz": "Ko'rsatish",
        "en": "Show",
    },
    "Изменить": {
        "uz": "O'zgartirish",
        "en": "Edit",
    },
    "Приход на склад (Остатки)": {
        "uz": "Omborga kirim (Qoldiqlar)",
        "en": "Inventory (Stock)",
    },
    "Удалить товар": {
        "uz": "Tovarni o'chirish",
        "en": "Delete product",
    },
    "Нет товаров": {
        "uz": "Tovarlar yo'q",
        "en": "No products",
    },
    "Вы еще не добавили ни одного товара.": {
        "uz": "Siz hali birorta ham tovar qo'shmadingiz.",
        "en": "You have not added any products yet.",
    },
    "Вы уверены, что хотите удалить этот товар навсегда?": {
        "uz": "Ushbu tovarni butunlay o'chirib tashlamoqchimisiz?",
        "en": "Are you sure you want to permanently delete this product?",
    },
    "Добавить новый товар": {
        "uz": "Yangi tovar qo'shish",
        "en": "Add New Product",
    },
    "Основная информация": {
        "uz": "Asosiy ma'lumotlar",
        "en": "Basic Information",
    },
    "Тип товара (размеры)": {
        "uz": "Tovar turi (o'lchamlar)",
        "en": "Product type (sizes)",
    },
    "Одежда, обувь и аксессуары (с выбором размеров)": {
        "uz": "Kiyim, poyabzal va aksessuarlar (o'lchamlari bilan)",
        "en": "Clothes, shoes & accessories (with sizes)",
    },
    "Электроника, книги, косметика, гигиена и др. (без размеров)": {
        "uz": "Elektronika, kitoblar, kosmetika, gigiyena va boshqalar (o'lchamsiz)",
        "en": "Electronics, books, cosmetics, hygiene, etc. (without sizes)",
    },
    "Категория товара": {
        "uz": "Tovar toifasi",
        "en": "Product Category",
    },
    "Выберите категорию...": {
        "uz": "Kategoriyani tanlang...",
        "en": "Select category...",
    },
    "Категории не созданы": {
        "uz": "Kategoriyalar yaratilmagan",
        "en": "No categories created",
    },
    "Название товара": {
        "uz": "Tovar nomi",
        "en": "Product Name",
    },
    "Описание товара": {
        "uz": "Tovar tavsifi",
        "en": "Product Description",
    },
    "Написать через ИИ": {
        "uz": "AI orqali yozish",
        "en": "Write with AI",
    },
    "ИИ пишет...": {
        "uz": "AI yozmoqda...",
        "en": "AI is writing...",
    },
    "Опишите товар: материал, особенности, комплектацию... или нажмите Написать через ИИ": {
        "uz": "Tovarni tasvirlang: material, xususiyatlar, to'plam... yoki 'AI orqali yozish'ni bosing",
        "en": "Describe the product: material, features, contents... or click Write with AI",
    },
    "Цена за 1 шт. (сум)": {
        "uz": "1 dona narxi (so'm)",
        "en": "Price per item (UZS)",
    },
    "Цвета, фото и остатки": {
        "uz": "Ranglar, rasmlar va qoldiqlar",
        "en": "Colors, photos and stock",
    },
    "Для каждого цвета загрузите от 2 до 10 фотографий. Добавьте размеры и количество на складе.": {
        "uz": "Har bir rang uchun 2 tadan 10 tagacha rasm yuklang. O'lchamlar va ombordagi sonini kiriting.",
        "en": "Upload 2 to 10 photos for each color. Add sizes and inventory quantity.",
    },
    "Добавить ещё один цвет": {
        "uz": "Yana bitta rang qo'shish",
        "en": "Add another color",
    },
    "Опубликовать товар": {
        "uz": "Tovarni chop etish",
        "en": "Publish Product",
    },

    # ─── 4.1. ФОТО ТОВАРОВ И ЗАГРУЗКА (PHOTO UPLOAD & VARIANTS) ───
    "Нажмите или перетащите фото": {
        "uz": "Rasm tanlash uchun bosing yoki bu yerga torting",
        "en": "Click or drag photos here",
    },
    "Вы можете выбрать сразу несколько (удерживайте Ctrl/Cmd)": {
        "uz": "Bir vaqtning o'zida bir nechta rasm tanlashingiz mumkin (Ctrl/Cmd ushlab turing)",
        "en": "You can select multiple photos (hold Ctrl/Cmd)",
    },
    "Выбрано фото:": {
        "uz": "Tanlangan rasmlar:",
        "en": "Selected photos:",
    },
    "Нажмите здесь, чтобы изменить выбор фото": {
        "uz": "Rasmlarni o'zgartirish uchun bu yerni bosing",
        "en": "Click here to change selected photos",
    },
    "Главное фото": {
        "uz": "Asosiy rasm",
        "en": "Cover photo",
    },
    "Отлично! Фото готовы к публикации": {
        "uz": "Ajoyib! Rasmlar chop etishga tayyor",
        "en": "Great! Photos are ready to publish",
    },
    "Выбрано только 1 фото. Рекомендуется от 2 до 10 фото!": {
        "uz": "Faqat 1 ta rasm tanlandi. 2 tadan 10 tagacha tavsiya etiladi!",
        "en": "Only 1 photo selected. 2 to 10 photos recommended!",
    },
    "Сбросить фото": {
        "uz": "Rasmlarni tozalash",
        "en": "Reset photos",
    },
    "Название цвета": {
        "uz": "Rang nomi",
        "en": "Color name",
    },
    "(Черный, Золотой, Стандарт...)": {
        "uz": "(Qora, Oltin, Standart...)",
        "en": "(Black, Gold, Standard...)",
    },
    "Черный": {
        "uz": "Qora",
        "en": "Black",
    },
    "Фотографии этого цвета": {
        "uz": "Ushbu rang rasmlari",
        "en": "Photos of this color",
    },
    "от 2 до 10 фото": {
        "uz": "2 tadan 10 tagacha rasm",
        "en": "from 2 to 10 photos",
    },
    "Сетка размеров и остатки:": {
        "uz": "O'lchamlar jadvali va qoldiqlar:",
        "en": "Size grid and stock:",
    },
    "Размер (S, M, L, 42...)": {
        "uz": "O'lcham (S, M, L, 42...)",
        "en": "Size (S, M, L, 42...)",
    },
    "+ Добавить размер": {
        "uz": "+ O'lcham qo'shish",
        "en": "+ Add size",
    },
    "Количество на складе (шт.)": {
        "uz": "Ombordagi soni (dona)",
        "en": "Quantity in stock (pcs)",
    },
    "Например: 15": {
        "uz": "Masalan: 15",
        "en": "For example: 15",
    },
    "Удалить этот цвет": {
        "uz": "Ushbu rangni o'chirish",
        "en": "Delete this color",
    },
    "Загружаем фото, создаем товар...": {
        "uz": "Rasmlar yuklanmoqda, tovar yaratilmoqda...",
        "en": "Uploading photos, creating product...",
    },
    "Для каждого цвета загрузите от 2 до 10 фотографий. Укажите общее количество товара на складе.": {
        "uz": "Har bir rang uchun 2 tadan 10 tagacha rasm yuklang. Ombordagi umumiy tovar miqdorini ko'rsating.",
        "en": "Upload 2 to 10 photos for each color. Specify total quantity in stock.",
    },
    "Пожалуйста, загрузите хотя бы 1 фото для цвета": {
        "uz": "Iltimos, rang uchun kamida 1 ta rasm yuklang",
        "en": "Please upload at least 1 photo for color",
    },
    "шт.": {
        "uz": "ta",
        "en": "pcs",
    },

    # ─── 5. ЗАКАЗЫ (ORDERS) ───
    "Управление заказами": {
        "uz": "Buyurtmalarni boshqarish",
        "en": "Orders Management",
    },
    "Оплачен (В сборке)": {
        "uz": "To'langan (Yig'ilmoqda)",
        "en": "Paid (Packaging)",
    },
    "У курьера (В пути)": {
        "uz": "Kuryerda (Yo'lda)",
        "en": "With Courier (In Transit)",
    },
    "Доставлен": {
        "uz": "Yetkazildi",
        "en": "Delivered",
    },
    "Ожидает оплаты": {
        "uz": "To'lov kutilmoqda",
        "en": "Awaiting Payment",
    },
    "Клиент": {
        "uz": "Mijoz",
        "en": "Customer",
    },
    "Адрес доставки": {
        "uz": "Yetkazib berish manzili",
        "en": "Delivery Address",
    },
    "Адрес уточняется": {
        "uz": "Manzil aniqlanmoqda",
        "en": "Address being clarified",
    },
    "Открыть на карте": {
        "uz": "Xaritada ochish",
        "en": "Open on Map",
    },
    "Состав заказа:": {
        "uz": "Buyurtma tarkibi:",
        "en": "Order items:",
    },
    "Трекинг доставки": {
        "uz": "Yetkazib berish kuzatuvi",
        "en": "Delivery Tracking",
    },
    "Открыть отслеживание заказа": {
        "uz": "Kuzatuv havolasini ochish",
        "en": "Open tracking link",
    },
    "Сумма заказа": {
        "uz": "Buyurtma summasi",
        "en": "Order Total",
    },
    "Прямой перевод на вашу карту (100% вам, 0% комиссии)": {
        "uz": "Kartangizga to'g'ridan-to'g'ri o'tkazma (100% sizga, 0% komissiya)",
        "en": "Direct transfer to your card (100% to you, 0% commission)",
    },
    "Подтвердить оплату на карту": {
        "uz": "Kartaga to'lovni tasdiqlash",
        "en": "Confirm card payment",
    },
    "Ссылка на доставку (Яндекс...)": {
        "uz": "Yetkazish havolasi (Yandex...)",
        "en": "Delivery link (Yandex...)",
    },
    "Передать курьеру": {
        "uz": "Kuryerga topshirish",
        "en": "Hand to courier",
    },
    "Подтвердить доставку": {
        "uz": "Yetkazilganini tasdiqlash",
        "en": "Confirm delivery",
    },
    "Заказов пока нет": {
        "uz": "Hozircha buyurtmalar yo'q",
        "en": "No orders yet",
    },
    "Как только кто-то оплатит покупку, она появится здесь.": {
        "uz": "Kimdir xarid uchun to'lov qilishi bilan u shu yerda paydo bo'ladi.",
        "en": "As soon as someone pays for an order, it will appear here.",
    },

    # ─── 6. НАСТРОЙКИ (SETTINGS) ───
    "Данные магазина и Брендинг": {
        "uz": "Do'kon ma'lumotlari va Brending",
        "en": "Store Info & Branding",
    },
    "Название магазина": {
        "uz": "Do'kon nomi",
        "en": "Store Name",
    },
    "Телефон (для клиентов)": {
        "uz": "Telefon (mijozlar uchun)",
        "en": "Phone (for customers)",
    },
    "Адрес магазина": {
        "uz": "Do'kon manzili",
        "en": "Store Address",
    },
    "Instagram аккаунт (без @)": {
        "uz": "Instagram akkaunt (@ siz)",
        "en": "Instagram account (without @)",
    },
    "Карта для приёма оплат от покупателей (Uzcard / Humo)": {
        "uz": "Xaridorlardan to'lov qabul qilish uchun karta (Uzcard / Humo)",
        "en": "Card for receiving customer payments (Uzcard / Humo)",
    },
    "Владелец карты (ФИО)": {
        "uz": "Karta egasi (F.I.SH)",
        "en": "Card holder (Full Name)",
    },
    "Персонализация дизайна": {
        "uz": "Dizaynni shaxsiylashtirish",
        "en": "Design Customization",
    },
    "Выберите одну из готовых премиальных тем или настройте свои фирменные цвета.": {
        "uz": "Tayyor mavzulardan birini tanlang yoki o'z brend ranglaringizni sozlang.",
        "en": "Choose one of the ready themes or customize your brand colors.",
    },
    "Готовые палитры от дизайнеров:": {
        "uz": "Dizaynerlar tayyorlagan palitralar:",
        "en": "Designer color palettes:",
    },
    "Свой дизайн (Фирменные цвета):": {
        "uz": "O'z dizayningiz (Brend ranglar):",
        "en": "Custom design (Brand colors):",
    },
    "Использовать свои цвета": {
        "uz": "O'z ranglarimdan foydalanish",
        "en": "Use custom colors",
    },
    "Основной цвет (Primary)": {
        "uz": "Asosiy rang (Primary)",
        "en": "Primary color",
    },
    "Акцентный цвет (Accent)": {
        "uz": "Qo'shimcha rang (Accent)",
        "en": "Accent color",
    },
    "Сохранить оформление витрины": {
        "uz": "Vitrina dizaynini saqlash",
        "en": "Save Storefront Design",
    },
    "Моя витрина": {
        "uz": "Mening vitrinam",
        "en": "My Storefront",
    },
    "Дата регистрации": {
        "uz": "Ro'yxatdan o'tgan sana",
        "en": "Registration date",
    },
    "BrainMart работает по фиксированной подписке. 0% комиссии с продаж. Никаких скрытых платежей.": {
        "uz": "BrainMart qat'iy obuna asosida ishlaydi. Savdolardan 0% komissiya. Hech qanday yashirin to'lovlarsiz.",
        "en": "BrainMart works on a fixed subscription. 0% commission on sales. No hidden fees.",
    },
    "Быстрые действия": {
        "uz": "Tezkor amallar",
        "en": "Quick Actions",
    },
    "Добавить товар": {
        "uz": "Tovar qo'shish",
        "en": "Add Product",
    },
    "Выйти из аккаунта": {
        "uz": "Hisobdan chiqish",
        "en": "Log out",
    },
    "Очистка истории и сброс данных": {
        "uz": "Tarixni tozalash va ma'lumotlarni qayta tiklash",
        "en": "Clear history & data reset",
    },
    "Если вы закончили тестирование кассы и витрины и хотите начать продажи с чистого листа, вы можете удалить все тестовые заказы, чеки кассы, смены и движения склада.": {
        "uz": "Kassa va vitrinani sinab ko'rishni tugatgan bo'lsangiz va savdoni toza varaqdan boshlamoqchi bo'lsangiz, barcha sinov buyurtmalari, kassa cheklari va ombor harakatlarini o'chirishingiz mumkin.",
        "en": "If you have finished testing POS and storefront and want to start fresh, you can clear all test orders, receipts, shifts, and warehouse movements.",
    },
    "Ваши товары, категории и настройки магазина останутся нетронутыми.": {
        "uz": "Tovarlaringiz, toifalar va do'kon sozlamalari saqlanib qoladi.",
        "en": "Your products, categories, and store settings will remain untouched.",
    },
    "Очистить историю заказов и кассы": {
        "uz": "Buyurtmalar va kassa tarixini tozalash",
        "en": "Clear orders and POS history",
    },
    "Вы действительно хотите очистить историю магазина?": {
        "uz": "Haqiqatan ham do'kon tarixini tozalamoqchimisiz?",
        "en": "Are you sure you want to clear store history?",
    },
    "Скопировано": {
        "uz": "Nusxalandi",
        "en": "Copied",
    },

    # ─── 7. БАЗОВЫЕ РАЗДЕЛЫ МЕНЮ И НАВИГАЦИИ (BASE.HTML) ───
    "Панель продавца": {
        "uz": "Sotuvchi paneli",
        "en": "Seller Dashboard",
    },
    "Главная": {
        "uz": "Bosh sahifa",
        "en": "Home",
    },
    "Товары": {
        "uz": "Tovarlar",
        "en": "Products",
    },
    "Заказы": {
        "uz": "Buyurtmalar",
        "en": "Orders",
    },
    "Клиенты": {
        "uz": "Mijozlar",
        "en": "Customers",
    },
    "Рассылки": {
        "uz": "Xabarnomalar",
        "en": "Broadcasts",
    },
    "Отзывы": {
        "uz": "Sharhlar",
        "en": "Reviews",
    },
    "Филиалы": {
        "uz": "Filiallar",
        "en": "Branches",
    },
    "Настройки": {
        "uz": "Sozlamalar",
        "en": "Settings",
    },
    "Открыть магазин": {
        "uz": "Do'konni ochish",
        "en": "Open Storefront",
    },
    "Инструкция": {
        "uz": "Qo'llanma",
        "en": "Instructions",
    },
    "Выход": {
        "uz": "Chiqish",
        "en": "Logout",
    },
    "Тариф:": {
        "uz": "Tarif:",
        "en": "Plan:",
    },
    "Комиссия:": {
        "uz": "Komissiya:",
        "en": "Commission:",
    },
    "Магазин онлайн": {
        "uz": "Do'kon onlayn",
        "en": "Store Online",
    },
    # ─── 14. ОФОРМЛЕНИЕ ЗАКАЗА, КОРЗИНА И ДОСТАВКА (CHECKOUT & CART) ───
    "Корзина": {
        "uz": "Savat",
        "en": "Cart",
    },
    "сум": {
        "uz": "so'm",
        "en": "UZS",
    },
    "Открыть": {
        "uz": "Ochish",
        "en": "Open",
    },
    "Ваша корзина": {
        "uz": "Sizning savatingiz",
        "en": "Your cart",
    },
    "Количество товаров:": {
        "uz": "Mahsulotlar soni:",
        "en": "Items quantity:",
    },
    "шт.": {
        "uz": "dona",
        "en": "pcs",
    },
    "Итого к оплате:": {
        "uz": "To'lov uchun jami:",
        "en": "Total to pay:",
    },
    "Данные для доставки такси": {
        "uz": "Taksi yetkazib berish ma'lumotlari",
        "en": "Taxi delivery details",
    },
    "ВАЖНО: Продавец в этой локации закажет такси, поэтому вы должны указать её максимально точно!": {
        "uz": "MUHIM: Sotuvchi ushbu manzilga taksi chaqiradi, shuning uchun uni iloji boricha aniq ko'rsating!",
        "en": "IMPORTANT: The seller will order a taxi to this location, so please specify it as accurately as possible!",
    },
    "Ваше имя и фамилия": {
        "uz": "Ismingiz va familiyangiz",
        "en": "Your full name",
    },
    "Например: Азиз Каримов": {
        "uz": "Masalan: Aziz Karimov",
        "en": "e.g. Aziz Karimov",
    },
    "Номер телефона для курьера": {
        "uz": "Kuryer uchun telefon raqami",
        "en": "Phone number for courier",
    },
    "Определить где я по GPS (Точно)": {
        "uz": "GPS orqali joylashuvimni aniqlash (Aniq)",
        "en": "Detect my location via GPS (Accurate)",
    },
    "Определяем координаты...": {
        "uz": "Koordinatalar aniqlanmoqda...",
        "en": "Detecting coordinates...",
    },
    "Точные координаты получены": {
        "uz": "Aniq koordinatalar olindi",
        "en": "Exact coordinates received",
    },
    "Ошибка GPS. Введите адрес вручную": {
        "uz": "GPS xatosi. Manzilni qo'lda kiriting",
        "en": "GPS error. Enter address manually",
    },
    "Геолокация не поддерживается вашим браузером. Пожалуйста, напишите адрес вручную.": {
        "uz": "Brauzeringiz geolokatsiyani qo'llab-quvvatlamaydi. Iltimos, manzilni qo'lda yozing.",
        "en": "Geolocation is not supported by your browser. Please enter the address manually.",
    },
    "Не удалось получить геолокацию. Пожалуйста, введите точный адрес и ориентир.": {
        "uz": "Geolokatsiyani aniqlab bo'lmadi. Iltimos, aniq manzil va mo'ljalni kiriting.",
        "en": "Failed to get geolocation. Please enter the exact address and landmark.",
    },
    "Адрес доставки (улица, дом / квартира)": {
        "uz": "Yetkazib berish manzili (ko'cha, uy / xonadon)",
        "en": "Delivery address (street, house / apt)",
    },
    "Ташкент, ул. Навои, 14, кв. 28": {
        "uz": "Toshkent, Navoiy ko'chasi, 14, 28-xonadon",
        "en": "Tashkent, Navoi st, 14, apt 28",
    },
    "Ориентир для такси (подъезд, этаж, вывеска рядом)": {
        "uz": "Taksi uchun mo'ljal (podyezd, qavat, yaqin atrofdagi belgi)",
        "en": "Taxi landmark (entrance, floor, nearby landmark)",
    },
    "Подъезд 3, домофон 28, рядом с Korzinka": {
        "uz": "3-podyezd, 28-domofon, Korzinka yonida",
        "en": "Entrance 3, intercom 28, near Korzinka",
    },
    "Способ оплаты": {
        "uz": "To'lov usuli",
        "en": "Payment method",
    },
    "Перевод на карту продавца (с чеком)": {
        "uz": "Sotuvchining kartasiga o'tkazma (chek bilan)",
        "en": "Direct transfer to seller card (with receipt)",
    },
    "Uzcard / Humo с прикреплением фото чека": {
        "uz": "Chek rasmini yuklagan holda Uzcard / Humo",
        "en": "Uzcard / Humo with payment receipt upload",
    },
    "Основной": {
        "uz": "Asosiy",
        "en": "Primary",
    },
    "Онлайн-эквайринг картой (Click/Payme)": {
        "uz": "Karta orqali onlayn to'lov (Click/Payme)",
        "en": "Online card acquiring (Click/Payme)",
    },
    "Временно на доработке (Скоро)": {
        "uz": "Vaqtinchalik yangilanmoqda (Tez orada)",
        "en": "Temporarily under maintenance (Soon)",
    },
    "В разработке": {
        "uz": "Jarayonda",
        "en": "In progress",
    },
    "⚙️ Автоматический эквайринг сейчас находится на технической доработке.\\n\\nПожалуйста, выберите прямой перевод на карту с отправкой фото чека — продавец лично проверит платёж перед отправкой!": {
        "uz": "⚙️ Avtomatik ekvayring texnik yangilanishda.\\n\\nIltimos, kartaga to'g'ridan-to'g'ri o'tkazmani tanlang va chek rasmini yuklang — sotuvchi yuborishdan oldin to'lovni shaxsan tekshiradi!",
        "en": "⚙️ Online acquiring is undergoing technical updates.\\n\\nPlease choose direct card transfer with receipt photo upload — the seller will verify the payment before dispatching!",
    },
    "Получить карту продавца и прикрепить чек": {
        "uz": "Sotuvchi kartasini olish va chekni biriktirish",
        "en": "Get seller card & attach receipt",
    },
    "Завершить заказ через Telegram": {
        "uz": "Buyurtmani Telegram orqali yakunlash",
        "en": "Complete order via Telegram",
    },
    "Заказ мгновенно поступит продавцу для отправки такси": {
        "uz": "Buyurtma darhol taksi chaqirish uchun sotuvchiga yetib boradi",
        "en": "The order is instantly sent to the seller for taxi dispatch",
    },
    "Корзина пуста": {
        "uz": "Savat bo'sh",
        "en": "Cart is empty",
    },
    "Удалить": {
        "uz": "O'chirish",
        "en": "Delete",
    },
    "Пожалуйста, укажите ваш номер телефона для связи!": {
        "uz": "Iltimos, bog'lanish uchun telefon raqamingizni kiriting!",
        "en": "Please provide your phone number for contact!",
    },
    "Пожалуйста, укажите адрес доставки или нажмите кнопку определения GPS!": {
        "uz": "Iltimos, yetkazish manzilini kiriting yoki GPS tugmasini bosing!",
        "en": "Please specify the delivery address or press the GPS button!",
    },
    "Оформляем заказ...": {
        "uz": "Buyurtma rasmiylashtirilmoqda...",
        "en": "Placing order...",
    },
    "Заказ успешно создан!": {
        "uz": "Buyurtma muvaffaqiyatli yaratildi!",
        "en": "Order created successfully!",
    },
    "Оплатить заказ онлайн сразу": {
        "uz": "Buyurtmani hoziroq onlayn to'lash",
        "en": "Pay order online now",
    },
    "Ошибка": {
        "uz": "Xatolik",
        "en": "Error",
    },
    "Не удалось оформить заказ": {
        "uz": "Buyurtmani rasmiylashtirib bo'lmadi",
        "en": "Failed to place order",
    },
    "Ошибка соединения с сервером.": {
        "uz": "Server bilan aloqa xatosi.",
        "en": "Server connection error.",
    },
    "Отправляем в бот...": {
        "uz": "Botga yuborilmoqda...",
        "en": "Sending to bot...",
    },
    "Ошибка сети": {
        "uz": "Tarmoq xatosi",
        "en": "Network error",
    },
    "Сбой": {
        "uz": "Nosozlik",
        "en": "Failure",
    },
    # ─── 15. ПОДТВЕРЖДЕНИЕ ЗАКАЗА (ORDER SUCCESS) ───
    "Заказ успешно оформлен!": {
        "uz": "Buyurtma muvaffaqiyatli rasmiylashtirildi!",
        "en": "Order placed successfully!",
    },
    "Заказ передан продавцу магазина «": {
        "uz": "Buyurtma «",
        "en": "Order handed to seller of «",
    },
    "»": {
        "uz": "» do'koni sotuvchisiga topshirildi",
        "en": "» store",
    },
    "Оплачено онлайн (Авто-эквайринг)": {
        "uz": "Onlayn to'langan (Avto-ekvayring)",
        "en": "Paid online (Auto-acquiring)",
    },
    "Оплата мгновенно подтверждена! Чек сформирован автоматически, загрузка фото не требуется. Магазин уже собирает заказ и вызывает такси.": {
        "uz": "To'lov darhol tasdiqlandi! Chek avtomatik shakllantirildi, rasm yuklash shart emas. Do'kon allaqachon buyurtmani yig'moqda va taksi chaqirmoqda.",
        "en": "Payment instantly confirmed! Receipt generated automatically, no photo required. The store is already packing your order and calling a taxi.",
    },
    "Транзакция": {
        "uz": "Tranzaksiya",
        "en": "Transaction",
    },
    "Ответственный менеджер магазина": {
        "uz": "Do'konning mas'ul menejeri",
        "en": "Responsible store manager",
    },
    "Владелец магазина": {
        "uz": "Do'kon egasi",
        "en": "Store owner",
    },
    "Мы передали заказ сотрудникам магазина. Вы также можете напрямую связаться с ними в Telegram:": {
        "uz": "Biz buyurtmani do'kon xodimlariga topshirdik. Shuningdek, ular bilan Telegram orqali to'g'ridan-to'g'ri bog'lanishingiz mumkin:",
        "en": "We forwarded the order to the store team. You can also contact them directly on Telegram:",
    },
    "Написать менеджеру в Telegram": {
        "uz": "Telegramda menejerga yozish",
        "en": "Message manager on Telegram",
    },
    "КАРТА ПРОДАВЦА ДЛЯ ОПЛАТЫ": {
        "uz": "TO'LOV UCHUN SOTUVCHI KARTASI",
        "en": "SELLER'S CARD FOR PAYMENT",
    },
    "Точная сумма к переводу:": {
        "uz": "O'tkazish uchun aniq summa:",
        "en": "Exact transfer amount:",
    },
    "Скопировать номер карты": {
        "uz": "Karta raqamini nusxalash",
        "en": "Copy card number",
    },
    "Скопировано!": {
        "uz": "Nusxalandi!",
        "en": "Copied!",
    },
    "Переведите точную сумму на карту продавца через Payme, Click или банк. Продавец свяжется с вами по телефону для подтверждения отправки такси!": {
        "uz": "Payme, Click yoki bank orqali sotuvchining kartasiga aniq summani o'tkazing. Sotuvchi taksi yuborilishini tasdiqlash uchun siz bilan telefon orqali bog'lanadi!",
        "en": "Transfer the exact amount to seller card via Payme, Click, or bank. The seller will call you to confirm taxi dispatch!",
    },
    "Оплата подтверждена магазином!": {
        "uz": "To'lov do'kon tomonidan tasdiqlandi!",
        "en": "Payment confirmed by store!",
    },
    "Деньги поступили на карту. Заказ собирается и будет отправлен курьером на указанный адрес.": {
        "uz": "Pul kartaga tushdi. Buyurtma yig'ilmoqda va ko'rsatilgan manzilga kuryer orqali yuboriladi.",
        "en": "Funds received. The order is being packed and will be dispatched by courier to your address.",
    },
    "Менеджер магазина:": {
        "uz": "Do'kon menejeri:",
        "en": "Store manager:",
    },
    "Владелец магазина:": {
        "uz": "Do'kon egasi:",
        "en": "Store owner:",
    },
    "Чек перевода успешно отправлен!": {
        "uz": "O'tkazma cheki muvaffaqiyatli yuborildi!",
        "en": "Transfer receipt sent successfully!",
    },
    "Мы перенаправили заказ и чек сотрудникам магазина «": {
        "uz": "Biz buyurtma va chekni «",
        "en": "We forwarded the order and receipt to «",
    },
    "». Скоро вам привезут товар!": {
        "uz": "» do'koni xodimlariga topshirdik. Tez orada mahsulot yetkaziladi!",
        "en": "» store team. Your order will arrive soon!",
    },
    "Мы уже уведомили сотрудника в Telegram. Вы также можете напрямую связаться с ним для любых уточнений по доставке:": {
        "uz": "Biz xodimni Telegram orqali xabardor qildik. Yetkazib berish bo'yicha har qanday savollar uchun u bilan to'g'ridan-to'g'ri bog'lanishingiz mumkin:",
        "en": "We have notified the staff via Telegram. You can also contact them directly for any delivery updates:",
    },
    "Прикрепите фото перевода": {
        "uz": "O'tkazma rasmini biriktiring",
        "en": "Attach payment receipt photo",
    },
    "Обязательно:": {
        "uz": "Majburiy:",
        "en": "Required:",
    },
    "чтобы магазин получил ваш заказ и отправил курьера, прикрепите фото или скриншот чека перевода из Payme/Click/банка:": {
        "uz": "do'kon buyurtmangizni qabul qilib kuryer yuborishi uchun Payme/Click/bankdan o'tkazma cheki fotosurati yoki skrinshotini biriktiring:",
        "en": "for the store to receive your order and dispatch courier, attach a photo or screenshot of the transfer receipt from Payme/Click/bank:",
    },
    "Нажмите, чтобы выбрать фото чека": {
        "uz": "Chek rasmini tanlash uchun bosing",
        "en": "Tap to choose receipt photo",
    },
    "Без фото чека заказ не может быть отправлен": {
        "uz": "Chek rasmi bo'lmasa buyurtma yuborilmaydi",
        "en": "Order cannot be dispatched without a receipt photo",
    },
    "Чек выбран!": {
        "uz": "Chek tanlandi!",
        "en": "Receipt selected!",
    },
    "Нажмите, чтобы заменить": {
        "uz": "Almashtirish uchun bosing",
        "en": "Tap to replace",
    },
    "Сначала прикрепите фото чека": {
        "uz": "Avval chek rasmini biriktiring",
        "en": "First attach receipt photo",
    },
    "Отправить чек продавцу": {
        "uz": "Chekni sotuvchiga yuborish",
        "en": "Send receipt to seller",
    },
    "Пожалуйста, обязательно выберите фото или скриншот чека перед отправкой!": {
        "uz": "Iltimos, yuborishdan oldin albatta chek rasmi yoki skrinshotini tanlang!",
        "en": "Please be sure to select a receipt photo or screenshot before sending!",
    },
    "Отправка чека...": {
        "uz": "Chek yuborilmoqda...",
        "en": "Sending receipt...",
    },
    "Заказ": {
        "uz": "Buyurtma",
        "en": "Order",
    },
    "Оплачен": {
        "uz": "To'langan",
        "en": "Paid",
    },
    "Чек на проверке": {
        "uz": "Chek tekshirilmoqda",
        "en": "Receipt under review",
    },
    "Ожидает перевода на карту": {
        "uz": "Kartaga o'tkazma kutilmoqda",
        "en": "Awaiting card transfer",
    },
    "Магазин:": {
        "uz": "Do'kon:",
        "en": "Store:",
    },
    "Получатель:": {
        "uz": "Qabul qiluvchi:",
        "en": "Recipient:",
    },
    "Телефон:": {
        "uz": "Telefon:",
        "en": "Phone:",
    },
    "Адрес доставки:": {
        "uz": "Yetkazib berish manzili:",
        "en": "Delivery address:",
    },
    "Состав заказа:": {
        "uz": "Buyurtma tarkibi:",
        "en": "Order items:",
    },
    "Итого:": {
        "uz": "Jami:",
        "en": "Total:",
    },
    "Вернуться в каталог": {
        "uz": "Katalogga qaytish",
        "en": "Back to catalog",
    },
    # ─── 16. КАРТОЧКА ТОВАРА (PRODUCT DETAIL) ───
    "В корзину": {
        "uz": "Savatga",
        "en": "Add to cart",
    },
    "Оформить": {
        "uz": "Rasmiylashtirish",
        "en": "Checkout",
    },
    "Не выбран": {
        "uz": "Tanlanmagan",
        "en": "Not selected",
    },
    "Цвет:": {
        "uz": "Rangi:",
        "en": "Color:",
    },
    "Размер:": {
        "uz": "O'lchami:",
        "en": "Size:",
    },
    "Нет доступных вариантов": {
        "uz": "Mavjud variantlar yo'q",
        "en": "No variants available",
    },
    "О товаре": {
        "uz": "Mahsulot haqida",
        "en": "About product",
    },
    "Характеристики": {
        "uz": "Xususiyatlari",
        "en": "Specifications",
    },
    "Страна: Узбекистан": {
        "uz": "Mamlakat: O'zbekiston",
        "en": "Country: Uzbekistan",
    },
    "Артикул:": {
        "uz": "Artikul:",
        "en": "SKU:",
    },
    "Отзывы покупателей": {
        "uz": "Xaridorlar fikrlari",
        "en": "Customer reviews",
    },
    "Оставить отзыв": {
        "uz": "Fikr qoldirish",
        "en": "Leave a review",
    },
    "Ваш отзыв о товаре": {
        "uz": "Mahsulot haqida fikringiz",
        "en": "Your product review",
    },
    "Пока нет отзывов": {
        "uz": "Hozircha fikrlar yo'q",
        "en": "No reviews yet",
    },
    "Будьте первым, кто оставит отзыв об этом товаре!": {
        "uz": "Ushbu mahsulot haqida birinchi bo'lib fikr bildiring!",
        "en": "Be the first to review this product!",
    },
    "человек купили на этой неделе": {
        "uz": "kishi shu hafta sotib oldi",
        "en": "people bought this week",
    },
    "отзывов": {
        "uz": "ta sharh",
        "en": "reviews",
    },
    "Более 100+ заказов": {
        "uz": "100+ dan ortiq buyurtmalar",
        "en": "Over 100+ orders",
    },
    # ─── 17. СЕРВЕРНЫЕ СООБЩЕНИЯ ВАЛИДАЦИИ (BACKEND VALIDATION) ───
    "Корзина пуста.": {
        "uz": "Savat bo'sh.",
        "en": "Cart is empty.",
    },
    "Пожалуйста, укажите номер телефона для связи.": {
        "uz": "Iltimos, bog'lanish uchun telefon raqamini kiriting.",
        "en": "Please provide a contact phone number.",
    },
    "Укажите адрес доставки или отправьте локацию.": {
        "uz": "Yetkazib berish manzilini kiriting yoki lokatsiyani yuboring.",
        "en": "Please specify delivery address or send location.",
    },
    "Один из товаров в корзине больше не доступен на складе.": {
        "uz": "Savatdagi mahsulotlardan biri omborda qolmagan.",
        "en": "One of the items in cart is no longer available in stock.",
    },
    "Товар не найден на складе.": {
        "uz": "Mahsulot omborda topilmadi.",
        "en": "Product not found in stock.",
    },
    "⚠️ Пожалуйста, обязательно прикрепите фото или скриншот чека об оплате!": {
        "uz": "⚠️ Iltimos, albatta to'lov cheki fotosurati yoki skrinshotini biriktiring!",
        "en": "⚠️ Please be sure to attach a photo or screenshot of the payment receipt!",
    },
    "✅ Чек успешно передан магазину! Скоро заказ будет доставлен.": {
        "uz": "✅ Chek do'konga muvaffaqiyatli topshirildi! Tez orada buyurtma yetkaziladi.",
        "en": "✅ Receipt successfully forwarded to store! The order will be delivered soon.",
    },
    # ─── 18. НАСТРОЙКИ МАГАЗИНА (SETTINGS) ───
    "Язык уведомлений в Telegram": {
        "uz": "Telegram bildirishnomalari tili",
        "en": "Telegram notification language",
    },
    "Выберите, на каком языке вам будут приходить карточки заказов, кнопки и уведомления в Telegram-боте:": {
        "uz": "Telegram-botda buyurtma kartochkalari, tugmalar va bildirishnomalar qaysi tilda kelishini tanlang:",
        "en": "Choose the language in which you will receive order cards, buttons, and notifications in the Telegram bot:",
    },
}


def parse_po(file_path):
    """Parses a PO file into a dictionary of {msgid: msgstr} and header string."""
    entries = {}
    header = ""
    if not os.path.exists(file_path):
        return entries, header

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Match msgid and msgstr pairs
    pattern = re.compile(
        r'msgid\s+(".*?(?<!\\)")(?:\s*\n".*?(?<!\\)")*\s*\n'
        r'msgstr\s+(".*?(?<!\\)")(?:\s*\n".*?(?<!\\)")*',
        re.MULTILINE
    )

    def unescape(text):
        parts = re.findall(r'"((?:[^"\\]|\\.)*)"', text)
        raw = "".join(parts)
        return raw.replace(r'\\', '\\').replace(r'\"', '"').replace(r'\n', '\n')

    for match in re.finditer(r'msgid\s+((?:".*?"\s*)+)\s*msgstr\s+((?:".*?"\s*)+)', content):
        raw_id = match.group(1)
        raw_str = match.group(2)
        k = unescape(raw_id)
        v = unescape(raw_str)
        if k == "":
            header = v
        else:
            entries[k] = v

    return entries, header


def escape_po(text):
    """Escapes text for PO file output."""
    t = text.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
    return f'"{t}"'


def write_po(file_path, entries, header_comment=""):
    """Writes dictionary of entries to PO file."""
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(header_comment or '# Translations\n')
        f.write('msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n"Content-Transfer-Encoding: 8bit\\n"\n\n')
        for k, v in sorted(entries.items()):
            if not k:
                continue
            f.write(f'msgid {escape_po(k)}\n')
            f.write(f'msgstr {escape_po(v)}\n\n')


def generate_mo(messages):
    """Compiles dictionary of {msgid: msgstr} into MO binary bytes."""
    keys = sorted(messages.keys())
    offsets = []
    ids = b""
    strs = b""

    for k in keys:
        k_b = k.encode("utf-8")
        v_b = messages[k].encode("utf-8")
        offsets.append((len(ids), len(k_b), len(strs), len(v_b)))
        ids += k_b + b"\0"
        strs += v_b + b"\0"

    keystart = 7 * 4 + len(keys) * 8 * 2
    valuestart = keystart + len(ids)

    koffsets = []
    voffsets = []
    for o1, l1, o2, l2 in offsets:
        koffsets += [l1, o1 + keystart]
        voffsets += [l2, o2 + valuestart]

    output = struct.pack(
        "Iiiiiii",
        0x950412DE,  # Magic
        0,           # Version
        len(keys),   # Number of strings
        7 * 4,       # Offset of table with original strings
        7 * 4 + len(keys) * 8,  # Offset of table with translation strings
        0,           # Size of hashing table
        0            # Offset of hashing table
    )
    output += struct.pack(str(len(koffsets)) + "i", *koffsets)
    output += struct.pack(str(len(voffsets)) + "i", *voffsets)
    output += ids + strs
    return output


def update_language(lang_code):
    po_path = LOCALE_DIR / lang_code / "LC_MESSAGES" / "django.po"
    mo_path = LOCALE_DIR / lang_code / "LC_MESSAGES" / "django.mo"

    po_path.parent.mkdir(parents=True, exist_ok=True)
    existing_entries, header = parse_po(po_path)

    added_count = 0
    updated_count = 0

    for original_ru, translations_map in list(TRANSLATIONS.items()):
        # Handle string as is, and also handle percent doubling (Django template % -> %%)
        variants = [(original_ru, translations_map)]
        if "%" in original_ru:
            doubled_ru = original_ru.replace("%", "%%")
            doubled_map = {
                lang: trans.replace("%", "%%") for lang, trans in translations_map.items()
            }
            variants.append((doubled_ru, doubled_map))

        for k_ru, t_map in variants:
            if lang_code in t_map:
                target_text = t_map[lang_code]
                if k_ru not in existing_entries:
                    existing_entries[k_ru] = target_text
                    added_count += 1
                else:
                    if not existing_entries[k_ru] or existing_entries[k_ru] == k_ru:
                        existing_entries[k_ru] = target_text
                        updated_count += 1
            elif lang_code == "ru":
                if k_ru not in existing_entries:
                    existing_entries[k_ru] = k_ru
                    added_count += 1

    # Also make sure all msgids have at least some translation
    clean_entries = {}
    for k, v in existing_entries.items():
        if k:
            clean_entries[k] = v or (k if lang_code == "ru" else "")

    # Write updated PO
    write_po(po_path, clean_entries, f'# Language: {lang_code}\n')

    # Compile to MO
    # For MO file, include only entries with non-empty translations
    mo_dict = {k: v for k, v in clean_entries.items() if v}
    mo_dict[""] = "Content-Type: text/plain; charset=UTF-8\n"
    mo_bytes = generate_mo(mo_dict)

    with open(mo_path, "wb") as f:
        f.write(mo_bytes)

    # Verify MO loading with gettext
    with open(mo_path, "rb") as f:
        gt = gettext.GNUTranslations(f)
    sample_key = "Точки и филиалы магазина"
    sample_trans = gt.gettext(sample_key)

    print(f"[{lang_code}] Total: {len(clean_entries)} strings | Added: {added_count} | Updated: {updated_count}")
    print(f"[{lang_code}] Sample test '{sample_key}' -> '{sample_trans}'")
    print(f"[{lang_code}] Saved to {mo_path} ({len(mo_bytes)} bytes)\n")


if __name__ == "__main__":
    print("Updating and compiling translations for ru, uz, en...")
    for lang in ["uz", "en", "ru"]:
        update_language(lang)
    print("All translations compiled successfully!")
