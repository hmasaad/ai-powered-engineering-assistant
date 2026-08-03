class ResponseEntity<T> {
  final T? data;
  final Exception? exception;

  const ResponseEntity(this.data, this.exception);

  factory ResponseEntity.success(T data) => ResponseEntity(data, null);

  factory ResponseEntity.failure(Exception exception) =>
      ResponseEntity(null, exception);

  bool get isSuccess => exception == null;
}

class DisplayableError implements Exception {
  final String errorMessage;

  DisplayableError(this.errorMessage);

  @override
  String toString() => errorMessage;
}
