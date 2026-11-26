import 'package:flutter/material.dart';
import 'package:get/get.dart';
import 'package:oasx/views/layout/appbar.dart';

import 'package:oasx/views/nav/view_nav.dart';
import 'package:oasx/views/layout/content.dart';

class DesktopLayoutView extends StatelessWidget {
  const DesktopLayoutView({Key? key}) : super(key: key);

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: buildPlatformAppBar(context),
      body: body(),
      floatingActionButton: FloatingActionButton(
        tooltip: '实时画面',
        onPressed: () => Get.toNamed('/live'),
        child: const Icon(Icons.monitor),
      ),
    );
  }

  Widget body() {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        const Nav(),
        const TreeMenuView(),
        Expanded(
          child: Center(child: content()),
        )
      ],
    );
  }
}
