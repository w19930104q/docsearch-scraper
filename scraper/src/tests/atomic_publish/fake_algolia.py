# coding: utf-8
"""
In-memory fake of the Algolia SearchClient used by AlgoliaHelper.

It mimics the small surface the scraper relies on (init_index, copy_rules,
move_index and the index methods) and keeps every index in a dict so tests
can inspect what would have been published.
"""
import copy


class FakeAlgoliaIndex:
    def __init__(self, client, name):
        self.client = client
        self.name = name

    @property
    def _state(self):
        return self.client.indexes[self.name]

    @property
    def records(self):
        return self._state['records']

    def save_objects(self, objects):
        self.client.calls.append(('save_objects', self.name, len(objects)))
        self.records.extend(copy.deepcopy(objects))

    def set_settings(self, settings):
        self.client.calls.append(('set_settings', self.name))
        self._state['settings'] = settings

    def save_rules(self, rules, *args):
        self.client.calls.append(('save_rules', self.name))
        self._state['rules'] = list(rules)

    def save_synonyms(self, synonyms):
        self.client.calls.append(('save_synonyms', self.name))
        self._state['synonyms'] = list(synonyms)

    def clear_objects(self):
        self.client.calls.append(('clear_objects', self.name))
        self._state['records'] = []


class FakeAlgoliaClient:
    def __init__(self):
        # index name -> {'records': [...], 'settings': ..., 'rules': [...]}
        self.indexes = {}
        # ordered log of every mutating call, for assertions
        self.calls = []

    def init_index(self, name):
        self.indexes.setdefault(name, {
            'records': [],
            'settings': None,
            'rules': [],
            'synonyms': []
        })
        return FakeAlgoliaIndex(self, name)

    def copy_rules(self, source, destination):
        self.calls.append(('copy_rules', source, destination))
        self.init_index(source)
        self.init_index(destination)
        self.indexes[destination]['rules'] = list(
            self.indexes[source]['rules'])

    def move_index(self, source, destination):
        self.calls.append(('move_index', source, destination))
        self.init_index(source)
        self.init_index(destination)
        # An Algolia move atomically replaces the destination index
        self.indexes[destination]['records'] = list(
            self.indexes[source]['records'])
        self.indexes[destination]['settings'] = self.indexes[source][
            'settings']
        self.indexes[destination]['rules'] = list(
            self.indexes[source]['rules'])

    # -- assertions helpers -------------------------------------------------

    def moved_indexes(self):
        """(source, destination) pairs of every move_index call"""
        return [call[1:3] for call in self.calls if call[0] == 'move_index']

    def records_of(self, name):
        return self.indexes[name]['records']
