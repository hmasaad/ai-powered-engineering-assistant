abstract class ViewAction {}

class InitiateAction extends ViewAction {
  final String target;
  Object? data;

  InitiateAction(this.target, {this.data});
}

class DisplayMessage extends ViewAction {
  final String message;

  DisplayMessage(this.message);
}

class CloseScreen extends ViewAction {}

class NavigateScreen extends ViewAction {
  final String target;
  Object? data;

  NavigateScreen(this.target, {this.data});
}
