import unittest
from types import SimpleNamespace

from bot.utils.challenge import CHALLENGE_ATTACHMENT_FILENAMES, arrange_challenge_attachments


class ChallengeAttachmentTests(unittest.TestCase):
    def test_arranges_files_by_name(self):
        attachments = [SimpleNamespace(filename=name) for name in reversed(CHALLENGE_ATTACHMENT_FILENAMES)]

        arranged = arrange_challenge_attachments(attachments)

        self.assertEqual(tuple(arranged), CHALLENGE_ATTACHMENT_FILENAMES)

    def test_reports_invalid_file_set(self):
        attachments = [SimpleNamespace(filename=name) for name in CHALLENGE_ATTACHMENT_FILENAMES[:-1]]
        attachments.append(SimpleNamespace(filename="wrong.json"))

        with self.assertRaisesRegex(ValueError, "missing: expected-outputs.json; unexpected: wrong.json"):
            arrange_challenge_attachments(attachments)


if __name__ == "__main__":
    unittest.main()
