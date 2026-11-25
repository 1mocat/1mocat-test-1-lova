#!/usr/bin/env python3
"""Python re-implementation of src/program.cbl (BOOKFORUM).

Reads one fixed-width request from src/INPUT.DAT (buyer id, seller id, book id,
amount). If buyer and seller exist in data/ACCOUNTS.DAT and the book exists in
data/BOOKS.DAT and is owned by the seller, the amount is moved from the buyer's
balance to the seller's, the book's owner becomes the buyer, and a record is
appended to data/TRANSACTIONS.DAT.

All files are fixed-length record files without separators. The file handling
below mirrors the GnuCOBOL 3 runtime (libcob), including its behaviour for
malformed data (short records, non-digit characters in numeric fields), so the
resulting .DAT files are byte-identical to the ones the COBOL program produces.

Run from the cobol/ directory:  python3 program.py
"""
import os
import sys

ACCOUNT_LEN = 34   # ACCOUNT-ID X(4) + ACCOUNT-NAME X(20) + ACCOUNT-BALANCE 9(10)
BOOK_LEN = 28      # BOOK-ID X(4) + BOOK-TITLE X(20) + BOOK-OWNER X(4)
TRANS_LEN = 22     # TRANS-BOOK X(4) + TRANS-AMOUNT 9(10) + SELLER X(4) + BUYER X(4)
INPUT_LEN = 22     # BUYER X(4) + SELLER X(4) + BOOK X(4) + INPUT-AMOUNT 9(10)
MOD = 10 ** 10     # PIC 9(10): results are truncated to ten digits


class RuntimeAbort(Exception):
    """A libcob runtime error: the run unit stops with exit status 1."""


class SeqFile:
    """A fixed-length SEQUENTIAL file as handled by libcob.

    The record area and the "current record size" live as long as the program:
    a short trailing record only overwrites the start of the record area and
    permanently shrinks the size used by later READs (and makes REWRITE fail).
    """

    def __init__(self, name, path, record_len):
        self.name = name
        self.path = path
        self.record_len = record_len
        self.record = bytearray(record_len)   # record area, initially NULs
        self.size = record_len
        self.fh = None
        self.read_done = False

    def _abort(self, message, status):
        raise RuntimeAbort("libcob: error: %s (status = %d) for file %s ('%s')"
                           % (message, status, self.name, self.path))

    def open(self, mode):
        if not os.path.isfile(self.path):
            self._abort("file does not exist", 35)
        try:
            self.fh = open(self.path, mode, buffering=0)
        except PermissionError:
            self._abort("permission denied", 37)
        except OSError:
            self._abort("permanent file error", 30)
        self.read_done = False

    def close(self):
        if self.fh is not None:
            self.fh.close()
            self.fh = None

    def read(self):
        """READ next record into the record area. Returns False at end."""
        self.read_done = False
        chunk = self.fh.read(self.size)
        if not chunk:
            return False
        self.record[:len(chunk)] = chunk
        self.size = len(chunk)
        self.read_done = True
        return True

    def rewrite(self):
        read_done, self.read_done = self.read_done, False
        if not read_done:
            self._abort("READ must be executed first", 43)
        if self.size != self.record_len:
            self._abort("record overflow", 44)
        self.fh.seek(-self.size, os.SEEK_CUR)
        self.fh.write(bytes(self.record))


def display_value(field):
    """Value of an unsigned DISPLAY field as libcob computes it: every byte
    contributes its low nibble, even if it is not a digit."""
    value = 0
    for b in field:
        value = value * 10 + (b & 0x0F)
    return value


def move_numeric(field):
    """MOVE between unsigned DISPLAY items: bytes are copied unchanged except
    that spaces and NULs become zeros."""
    return bytes(0x30 if b in (0x20, 0x00) else b for b in field)


def store_balance(value):
    """Store an arithmetic result in PIC 9(10): sign dropped, high digits cut."""
    return b"%010d" % (abs(value) % MOD)


def base_dir():
    # The COBOL program resolves its files relative to the current directory
    # (cobol/). Fall back to this script's directory when run from elsewhere.
    if os.path.isfile(os.path.join("src", "INPUT.DAT")) or os.path.isdir("data"):
        return ""
    return os.path.dirname(os.path.abspath(__file__))


def main(out):
    base = base_dir()
    input_file = SeqFile("INPUT-FILE", os.path.join(base, "src", "INPUT.DAT"), INPUT_LEN)
    accounts = SeqFile("ACCOUNTS-FILE", os.path.join(base, "data", "ACCOUNTS.DAT"), ACCOUNT_LEN)
    books = SeqFile("BOOKS-FILE", os.path.join(base, "data", "BOOKS.DAT"), BOOK_LEN)
    trans_path = os.path.join(base, "data", "TRANSACTIONS.DAT")
    files = (input_file, accounts, books)

    try:
        # ---- MAIN-PARA: read the request ----------------------------------
        input_file.open("rb")
        if not input_file.read():
            out("Error: Input file is empty")
            return 0
        rec = bytes(input_file.record)
        buyer, seller, book = rec[0:4], rec[4:8], rec[8:12]
        amount_field = move_numeric(rec[12:22])      # WS-AMOUNT integer digits
        amount = display_value(amount_field)
        input_file.close()

        # ---- VALIDATE-USERS-AND-BOOK --------------------------------------
        buyer_found = seller_found = book_found = valid_owner = False
        accounts.open("rb")
        while accounts.read():
            if accounts.record[0:4] == buyer:
                buyer_found = True
            if accounts.record[0:4] == seller:
                seller_found = True
        accounts.close()

        books.open("rb")
        while books.read():
            if books.record[0:4] == book:
                book_found = True
                if books.record[24:28] == seller:
                    valid_owner = True
        books.close()
        out(" ")

        if not (buyer_found and seller_found and book_found and valid_owner):
            out("Transaction failed due to validation errors")
            return 0

        # ---- PROCESS-TRANSACTION ------------------------------------------
        accounts.open("r+b")
        while accounts.read():
            r = accounts.record
            if r[0:4] == buyer:
                r[24:34] = store_balance(display_value(r[24:34]) - amount)
                accounts.rewrite()
            if r[0:4] == seller:
                r[24:34] = store_balance(display_value(r[24:34]) + amount)
                accounts.rewrite()
        accounts.close()

        books.open("r+b")
        while books.read():
            if books.record[0:4] == book:
                books.record[24:28] = buyer
                books.rewrite()
        books.close()

        if not os.path.isfile(trans_path):
            raise RuntimeAbort(
                "libcob: error: file does not exist (status = 35) for file "
                "TRANSACTIONS-FILE ('%s')" % trans_path)
        with open(trans_path, "ab") as f:
            f.write(book + move_numeric(amount_field) + seller + buyer)

        out("Transaction completed successfully")
        return 0
    finally:
        for f in files:
            f.close()


if __name__ == "__main__":
    def _out(text):
        sys.stdout.write(text + "\n")
        sys.stdout.flush()

    try:
        code = main(_out)
    except RuntimeAbort as exc:
        sys.stderr.write(str(exc) + "\n")
        code = 1
    sys.exit(code)
