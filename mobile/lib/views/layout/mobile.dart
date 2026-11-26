import 'package:flutter/material.dart';
import 'package:get/get.dart';
import 'package:oasx/views/layout/appbar.dart';

import 'package:oasx/views/nav/view_nav.dart';
import 'package:oasx/views/layout/content.dart';
import 'package:styled_widget/styled_widget.dart';

class MobileLayoutView extends StatelessWidget {
  const MobileLayoutView({Key? key}) : super(key: key);

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: buildPlatformAppBar(context, isCollapsed: true),
      drawer: drawer(),
      body: body(),
      floatingActionButton: FloatingActionButton(
        tooltip: '实时画面',
        onPressed: () => Get.toNamed('/live'),
        child: const Icon(Icons.monitor),
      ),
    );
  }

  Widget body() {
    return content().center();
  }

  Widget drawer() {
    return const Row(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        Nav(),
        TreeMenuView(),
      ],
    );
  }
}
