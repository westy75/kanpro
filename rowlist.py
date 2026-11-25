"""A MenuList whose rows are padded and vertically centred (right-aligned in Arabic) like the app's lists.

The box's plain string list draws each label at the top-left corner of its row, so the rows are drawn
with a MultiContent text entry instead. Labels go in and come out exactly like a MenuList of strings.
"""
from enigma import RT_HALIGN_LEFT, RT_HALIGN_RIGHT, RT_VALIGN_CENTER, eListboxPythonMultiContent, gFont
from Components.MenuList import MenuList
from Components.MultiContent import MultiContentEntryText

PAD = 28


class RowList(MenuList):
    def __init__(self, labels, width, height, font, rtl):
        MenuList.__init__(self, [], content=eListboxPythonMultiContent)
        name, size = font.split(";")
        self.l.setFont(0, gFont(name, int(size)))
        self.l.setItemHeight(height)
        self.width, self.height = width, height
        self.flags = (RT_HALIGN_RIGHT if rtl else RT_HALIGN_LEFT) | RT_VALIGN_CENTER
        self.setList(labels)

    def setList(self, labels):
        MenuList.setList(self, [[label, MultiContentEntryText(pos=(PAD, 0), size=(self.width - 2 * PAD, self.height),
                                                              font=0, flags=self.flags, text=label)]
                                for label in labels])
