class PkError(Exception):
    def __init__(self, code, message, data=()):
        super().__init__(message)
        self.code, self.data = code, list(data)


CODES = {
    'NOTFOUND': 'name or file missing; use find',
    'AMBIGUOUS': 'multiple names; use an exact candidate',
    'BOUNDS': 'invalid coordinates or pagination; use valid bounds',
    'LIMIT': 'budget exceeded; reduce requested resources',
    'TEXTFIT': 'text does not fit; shorten it',
    'PARSE': 'invalid input; use help',
    'CONFLICT': 'source changed or transaction busy; reread and retry',
    'BUILD': 'build failed; fix reported errors and retry',
    'INTERNAL': 'unexpected failure; inspect .pk/error.log',
}
