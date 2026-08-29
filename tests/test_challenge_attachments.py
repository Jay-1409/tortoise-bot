import unittest
from types import SimpleNamespace

from bot.cogs.challenges import Challenges
from bot.utils.challenge import CHALLENGE_ATTACHMENT_FILENAMES, arrange_challenge_attachments


class ChallengeAttachmentTests(unittest.TestCase):
    def test_bulk_is_part_of_add_command(self):
        add = Challenges.challenge_group.get_command("add")
        parameters = {parameter.name: parameter.required for parameter in add.parameters}

        self.assertTrue(parameters["bulk"])
        self.assertFalse(parameters["statement"])
        self.assertIsNone(Challenges.challenge_group.get_command("add-bulk"))

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
