#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime
import mimetypes
import smtplib
import socket
import sys
from email.message import EmailMessage
from pathlib import Path


def _split_csv_values(values: list[str] | None) -> list[str]:
    if not values:
        return []

    items: list[str] = []
    for value in values:
        for chunk in value.split(","):
            cleaned = chunk.strip()
            if cleaned:
                items.append(cleaned)
    return items


def _load_recipients(to_file: str) -> list[str]:
    file_path = Path(to_file)
    if not file_path.exists():
        raise ValueError(f"Recipient file not found: {file_path}")

    recipients: list[str] = []
    for line in file_path.read_text(encoding="utf-8").splitlines():
        cleaned = line.strip()
        if cleaned and not cleaned.startswith("#"):
            recipients.append(cleaned)

    unique = list(dict.fromkeys(recipients))
    if not unique:
        raise ValueError(f"No recipients found in file: {file_path}")
    return unique


def _default_attachments() -> list[Path]:
    attachment = Path("Results2026.pdf")
    if attachment.exists():
        return [attachment]
    return []


def _load_attachments(attach_values: list[str] | None) -> list[Path]:
    requested = _split_csv_values(attach_values)
    if requested:
        attachments = [Path(value) for value in requested]
        if len(attachments) != 1:
            raise ValueError("Only one attachment is allowed, and it must be Results2026.pdf.")
        if attachments[0].name != "Results2026.pdf":
            raise ValueError("Only Results2026.pdf can be sent. Remove fallback/custom attachments.")
    else:
        attachments = _default_attachments()

    if not attachments:
        raise ValueError(
            "Results2026.pdf not found. Generate it first or pass --attach Results2026.pdf."
        )

    missing = [path for path in attachments if not path.exists()]
    if missing:
        missing_list = ", ".join(str(path) for path in missing)
        raise ValueError(f"Attachment file(s) not found: {missing_list}")

    return attachments


def _build_message(
    sender: str,
    recipients: list[str],
    subject: str,
    body: str,
    attachments: list[Path],
) -> EmailMessage:
    message = EmailMessage()
    message["From"] = sender
    # Keep recipient addresses private by using BCC only.
    message["To"] = "undisclosed-recipients:;"
    message["Bcc"] = ", ".join(recipients)
    message["Subject"] = subject
    message.set_content(body)

    date_text = datetime.now().strftime("%d-%m-%Y")

    for index, path in enumerate(attachments):
        content_type, encoding = mimetypes.guess_type(path.name)
        if content_type is None or encoding is not None:
            maintype, subtype = "application", "octet-stream"
        else:
            maintype, subtype = content_type.split("/", 1)

        with path.open("rb") as handle:
            data = handle.read()

        if path.suffix.lower() == ".pdf":
            display_name = f"Onsdagsbanen Kombinerede Resultater {date_text}.pdf"
            if index > 0:
                display_name = f"Onsdagsbanen Kombinerede Resultater {date_text} ({index + 1}).pdf"
        else:
            display_name = path.name

        message.add_attachment(data, maintype=maintype, subtype=subtype, filename=display_name)

    return message


def _confirm_send_with_popup(
    sender: str,
    recipients: list[str],
    subject: str,
    attachments: list[Path],
) -> bool:
    try:
        import tkinter as tk
        from tkinter import messagebox
    except Exception as exc:
        raise RuntimeError(
            "Could not open confirmation popup. Use --yes to skip popup confirmation."
        ) from exc

    attachment_list = ", ".join(path.name for path in attachments)
    message = (
        f"You are about to send an email to {len(recipients)} recipient(s) via BCC.\n\n"
        f"From: {sender}\n"
        f"Subject: {subject}\n"
        f"Attachments: {attachment_list}\n\n"
        "Do you really, really want to send this email to everybody?"
    )

    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass

    confirmed = messagebox.askyesno(
        title="Final Email Confirmation",
        message=message,
        icon="warning",
        default="no",
        parent=root,
    )
    root.destroy()
    return bool(confirmed)


def _prompt_app_password() -> str:
    """Prompt for Gmail app password in a paste-friendly way without CLI args/env vars."""
    # Prefer terminal prompt when available; GUI dialogs can be hidden on some Linux setups.
    if sys.stdin.isatty():
        try:
            return input("Gmail App Password (paste allowed): ").strip()
        except EOFError:
            return ""

    try:
        import tkinter as tk
        from tkinter import simpledialog

        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except Exception:
            pass

        password = simpledialog.askstring(
            title="Gmail App Password",
            prompt="Paste Gmail App Password:\n(input is hidden)",
            show="*",
            parent=root,
        )
        root.destroy()

        if password is None:
            return ""
        return password.strip()
    except Exception:
        # Terminal fallback still avoids history while allowing paste.
        try:
            return input("Gmail App Password (paste allowed): ").strip()
        except EOFError:
            return ""


def _load_credentials_from_file(credentials_file: str) -> tuple[str, str]:
    path = Path(credentials_file)
    if not path.exists() or not path.is_file():
        return "", ""

    sender = ""
    app_password = ""
    positional_values: list[str] = []

    for line in path.read_text(encoding="utf-8").splitlines():
        cleaned = line.strip()
        if not cleaned or cleaned.startswith("#"):
            continue

        if "=" in cleaned:
            key, value = cleaned.split("=", 1)
            key = key.strip().lower()
            value = value.strip()
            if not value:
                continue
            if key in {"from", "from_email", "sender", "email"}:
                sender = value
                continue
            if key in {"app_password", "password", "gmail_app_password"}:
                app_password = value
                continue

        positional_values.append(cleaned)

    if not sender and positional_values:
        first_value = positional_values[0]
        if "@" in first_value and " " not in first_value:
            sender = first_value
            positional_values = positional_values[1:]

    if not app_password and positional_values:
        app_password = positional_values[0]

    return sender, app_password


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Send result PDFs to recipients using a Gmail account.",
    )
    parser.add_argument(
        "--to-file",
        default="recipients.txt",
        help="Path to a text file with one recipient email per line (default: recipients.txt).",
    )
    parser.add_argument(
        "--attach",
        action="append",
        help=(
            "Attachment path. Only Results2026.pdf is allowed. "
            "Default is Results2026.pdf in the current folder."
        ),
    )
    parser.add_argument(
        "--subject",
        default="Manage2Sail Results",
        help="Email subject.",
    )
    parser.add_argument(
        "--body",
        default="Hej,\n\nHermed de nyeste kombinerede resultater fra onsdagsbanen.\n",
        help="Plain-text email body.",
    )
    parser.add_argument(
        "--from-email",
        help="Gmail address to send from. Overrides sender in credentials file.",
    )
    parser.add_argument(
        "--app-password-file",
        default="gmail_app_password.txt",
        help=(
            "Path to local credentials file. Supports sender email + app password. "
            "Default: gmail_app_password.txt"
        ),
    )
    parser.add_argument(
        "--smtp-host",
        default="smtp.gmail.com",
        help="SMTP host (default: smtp.gmail.com).",
    )
    parser.add_argument(
        "--smtp-port",
        type=int,
        default=465,
        help="SMTP SSL port (default: 465).",
    )
    parser.add_argument(
        "--smtp-timeout",
        type=float,
        default=20.0,
        help="SMTP connect/login timeout in seconds (default: 20).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate inputs and print what would be sent, without sending email.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the popup confirmation and send immediately.",
    )
    return parser


def run(args: argparse.Namespace) -> int:
    file_sender, file_password = _load_credentials_from_file(args.app_password_file)
    sender = args.from_email.strip() if args.from_email else file_sender

    recipients = _load_recipients(args.to_file)
    attachments = _load_attachments(args.attach)

    if args.dry_run:
        date_text = datetime.now().strftime("%d-%m-%Y")
        print("Dry run: email not sent.")
        print(f"From: {sender or '(not set)'}")
        print("To: undisclosed-recipients:;")
        print(f"Bcc: {', '.join(recipients)}")
        print(f"Recipients file: {args.to_file}")
        print(f"Subject: {args.subject}")
        print("Attachments:")
        for index, path in enumerate(attachments):
            if path.suffix.lower() == ".pdf":
                sent_as = f"Onsdagsbanen Kombinerede Resultater {date_text}.pdf"
                if index > 0:
                    sent_as = f"Onsdagsbanen Kombinerede Resultater {date_text} ({index + 1}).pdf"
            else:
                sent_as = path.name
            print(f" - {path} -> {sent_as}")
        return 0

    if not args.yes:
        confirmed = _confirm_send_with_popup(
            sender=sender,
            recipients=recipients,
            subject=args.subject,
            attachments=attachments,
        )
        if not confirmed:
            print("Send canceled in confirmation popup.")
            return 0

    if not sender:
        raise ValueError(
            "No sender email found. Provide --from-email or include sender email in credentials file."
        )

    app_password = file_password
    if not app_password:
        print(
            "No app password found in credentials file. "
            "Please paste your Gmail app password in the prompt."
        )
        app_password = _prompt_app_password()
    if not app_password:
        raise ValueError("No Gmail app password found in file or entered in prompt.")

    message = _build_message(
        sender=sender,
        recipients=recipients,
        subject=args.subject,
        body=args.body,
        attachments=attachments,
    )

    try:
        with smtplib.SMTP_SSL(args.smtp_host, args.smtp_port, timeout=float(args.smtp_timeout)) as smtp:
            smtp.login(sender, app_password)
            smtp.send_message(message, to_addrs=recipients)
    except (socket.timeout, TimeoutError) as exc:
        raise RuntimeError(
            f"SMTP connection timed out to {args.smtp_host}:{args.smtp_port}. "
            "Check network/firewall and try again, or increase --smtp-timeout."
        ) from exc
    except OSError as exc:
        raise RuntimeError(
            f"Could not reach SMTP server {args.smtp_host}:{args.smtp_port}: {exc}"
        ) from exc

    print(f"Email sent to {len(recipients)} recipient(s).")
    return 0


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    raise SystemExit(run(args))


if __name__ == "__main__":
    main()
